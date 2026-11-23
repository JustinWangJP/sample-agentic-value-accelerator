"""共通の経路表（modules/api_routes/routes.json）の形と、Lambda の呼び出し許可（source_arn）の重なりを固定する。

source_arn の * は / もまたいで一致する（README「dev に手動配信する」）。そのため管理 Lambda の
GET/api/records/*（GET /api/records/{recordId}）は、決裁 Lambda の GET の route にも一致する。
API Gateway は route の統合先の Lambda だけを呼び出すので、一致しても管理 Lambda が呼ばれることはない。
ただし一致する route が知らないうちに増えないよう、ここで集合を固定する。増えたら許可の形を見直し、
modules/api_routes/main.tf の aws_lambda_permission のコメントと README「dev に手動配信する」を合わせて直す。
"""

from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import unittest

INFRA = Path(__file__).resolve().parents[1]
ROUTES_JSON = INFRA / "modules/api_routes/routes.json"
# 実際の要求でパスパラメータに入る値の例。source_arn は要求の path で照合される
SAMPLE_PARAMETER = "rec0123456789"
PATH_PARAMETER = re.compile(r"\{[^}]+\}")
# REST API のメソッドとして割り当てられる形（ANY とパスの末尾の / は使わない）
METHODS = {"GET", "POST", "PATCH", "PUT", "DELETE"}
PATH_FORMAT = re.compile(r"^/api(/[A-Za-z0-9_-]+|/\{[A-Za-z]+\})+$")


def load_manifest() -> dict[str, dict[str, dict[str, str]]]:
    """本体と追加スタックが共有する経路表（グループ → 経路名 → method・path・statement_id）。"""
    return json.loads(ROUTES_JSON.read_text())


def routes_for(*groups: str) -> list[dict[str, str]]:
    manifest = load_manifest()
    return [route for group in groups for route in manifest[group].values()]


def route_key(route: dict[str, str]) -> str:
    return f"{route['method']} {route['path']}"


def invoke_pattern(route: dict[str, str]) -> str:
    """経路ごとに作る Lambda 呼び出し許可の source_arn のうち、stage の後ろ。"""
    return f"{route['method']}{PATH_PARAMETER.sub('*', route['path'])}"


def request_arn_path(route: dict[str, str]) -> str:
    """経路に届く要求の実行 ARN のうち、stage の後ろ。"""
    return f"{route['method']}{PATH_PARAMETER.sub(SAMPLE_PARAMETER, route['path'])}"


def matched(permitted: list[dict[str, str]], routes: list[dict[str, str]]) -> set[str]:
    """permitted の呼び出し許可のどれかに一致する routes の経路。"""
    patterns = [invoke_pattern(route) for route in permitted]
    return {
        route_key(route)
        for route in routes
        if any(fnmatchcase(request_arn_path(route), pattern) for pattern in patterns)
    }


class RouteManifestTests(unittest.TestCase):
    def test_groups_are_the_three_stacks(self):
        self.assertEqual(set(load_manifest()), {"main", "management", "decision"})

    def test_each_route_has_a_method_path_and_statement_id(self):
        for group, routes in load_manifest().items():
            for name, route in routes.items():
                with self.subTest(group=group, name=name):
                    self.assertEqual(set(route), {"method", "path", "statement_id"})
                    self.assertIn(route["method"], METHODS)
                    self.assertRegex(route["path"], PATH_FORMAT)
                    # パスパラメーターは 1 経路に 1 つまで（handler のルート表と同じ制約）
                    self.assertLessEqual(len(PATH_PARAMETER.findall(route["path"])), 1)
                    self.assertRegex(route["statement_id"], r"^[A-Za-z0-9_-]{1,100}$")

    def test_openapi_documents_exactly_the_routes(self):
        # OpenAPI 仕様（backend/openapi.yaml）の operation は、経路表の経路と一致させる。
        # CI の infrastructure ジョブは YAML のライブラリを持たないため、paths の字下げから読む
        lines = (INFRA.parent / "backend/openapi.yaml").read_text().splitlines()
        paths = lines[lines.index("paths:") + 1 : lines.index("components:")]
        documented, path = set(), None
        for line in paths:
            if match := re.fullmatch(r"  (/\S+):", line):
                path = match.group(1)
            elif match := re.fullmatch(r"    (get|post|put|patch|delete):", line):
                documented.add(f"{match.group(1).upper()} {path}")
        expected = {route_key(route) for route in routes_for("main", "management", "decision")}
        self.assertEqual(documented, expected)

    def test_all_route_keys_and_statement_ids_are_unique(self):
        routes = routes_for("main", "management", "decision")
        self.assertEqual(len({route_key(route) for route in routes}), 29)
        self.assertEqual(len({route["statement_id"] for route in routes}), 29)


class RoutePermissionTests(unittest.TestCase):
    def test_management_permissions_match_only_the_known_decision_routes(self):
        overlap = matched(routes_for("main", "management"), routes_for("decision"))
        self.assertEqual(
            overlap,
            {
                "GET /api/records/publication",
                "GET /api/records/{recordId}/publication",
                "GET /api/records/{recordId}/recovery",
            },
        )

    def test_decision_permissions_match_no_management_route(self):
        self.assertEqual(matched(routes_for("decision"), routes_for("main", "management")), set())

    def test_each_lambda_permission_covers_its_own_routes(self):
        for routes in (routes_for("main", "management"), routes_for("decision")):
            with self.subTest(routes=len(routes)):
                self.assertEqual(matched(routes, routes), {route_key(route) for route in routes})


if __name__ == "__main__":
    unittest.main()
