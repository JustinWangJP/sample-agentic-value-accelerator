"""Lambda の呼び出し許可（source_arn）が、ほかの Lambda に向く route のどれに一致するかを固定する。

source_arn の * は / もまたいで一致する（README「IAM と route」）。そのため管理 Lambda の
GET/api/records/*（GET /api/records/{recordId}）は、決裁 Lambda の GET の route にも一致する。
API Gateway は route の統合先の Lambda だけを呼び出すので、一致しても管理 Lambda が呼ばれることはない。
ただし一致する route が知らないうちに増えないよう、ここで集合を固定する。増えたら許可の形を見直し、
modules/management_routes/main.tf のコメントと README を合わせて直す。
"""

from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import unittest

INFRA = Path(__file__).resolve().parents[1]
# 実際の要求でパスパラメータに入る値の例。source_arn は要求の path で照合される
SAMPLE_PARAMETER = "rec0123456789"
PATH_PARAMETER = re.compile(r"\{[^}]+\}")


def routes_for(*groups: str) -> list[str]:
    """本体と追加スタックが共有する経路表から指定された経路を読む。"""
    manifest = json.loads((INFRA / "modules/management_routes/routes.json").read_text())
    return [route["route_key"] for group in groups for route in manifest[group].values()]


def invoke_patterns(routes: list[str]) -> list[str]:
    """REST API の統合ごとに作る Lambda 呼び出し許可のパターン。"""
    return [
        f"{method}/{PATH_PARAMETER.sub('*', path.removeprefix('/'))}"
        for method, path in (route.split(" ") for route in routes)
    ]


def request_arn_path(route: str) -> str:
    """route に届く要求の実行 ARN（stage の後ろ）。"""
    method, path = route.split(" ")
    return f"{method}/{PATH_PARAMETER.sub(SAMPLE_PARAMETER, path.removeprefix('/'))}"


def matched(patterns: list[str], routes: list[str]) -> set[str]:
    return {
        route
        for route in routes
        if any(fnmatchcase(request_arn_path(route), pattern) for pattern in patterns)
    }


class RoutePermissionTests(unittest.TestCase):
    def test_all_route_keys_and_statement_ids_are_unique(self):
        routes = routes_for("main", "management", "decision")
        manifest = json.loads((INFRA / "modules/management_routes/routes.json").read_text())
        statements = [route["statement_id"] for group in manifest.values() for route in group.values()]
        self.assertEqual(len(set(routes)), 29)
        self.assertEqual(len(set(statements)), 29)

    def test_management_permissions_match_only_the_known_decision_routes(self):
        decision_routes = routes_for("decision")
        overlap = matched(invoke_patterns(routes_for("main", "management")), decision_routes)
        self.assertEqual(
            overlap,
            {
                "GET /api/records/publication",
                "GET /api/records/{recordId}/publication",
                "GET /api/records/{recordId}/recovery",
            },
        )

    def test_decision_permissions_match_no_management_route(self):
        patterns = invoke_patterns(routes_for("decision"))
        self.assertEqual(matched(patterns, routes_for("main", "management")), set())

    def test_each_lambda_permission_covers_its_own_routes(self):
        decision_routes = routes_for("decision")
        routes = routes_for("main", "management")
        self.assertEqual(matched(invoke_patterns(routes), routes), set(routes))
        self.assertEqual(matched(invoke_patterns(decision_routes), decision_routes), set(decision_routes))


if __name__ == "__main__":
    unittest.main()
