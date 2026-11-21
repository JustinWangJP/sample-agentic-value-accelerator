"""Lambda の呼び出し許可（source_arn）が、ほかの Lambda に向く route のどれに一致するかを固定する。

source_arn の * は / もまたいで一致する（README「IAM と route」）。そのため管理 Lambda の
GET/api/records/*（GET /api/records/{recordId}）は、決裁 Lambda の GET の route にも一致する。
API Gateway は route の統合先の Lambda だけを呼び出すので、一致しても管理 Lambda が呼ばれることはない。
ただし一致する route が知らないうちに増えないよう、ここで集合を固定する。増えたら許可の形を見直し、
modules/management_routes/main.tf のコメントと README を合わせて直す。
"""

from fnmatch import fnmatchcase
from pathlib import Path
import re
import unittest

INFRA = Path(__file__).resolve().parents[1]
# 実際の要求でパスパラメータに入る値の例。source_arn は要求の path で照合される
SAMPLE_PARAMETER = "rec0123456789"
PATH_PARAMETER = re.compile(r"\{[^}]+\}")


def management_routes() -> list[str]:
    """管理 Lambda に向く route（本体スタックと management スタックの route 表）。"""
    sources = (INFRA / "main.tf").read_text() + (INFRA / "management/main.tf").read_text()
    routes = re.findall(r'route_key\s*=\s*"([A-Z]+ /api/[^"]+)"', sources)
    assert routes, "管理 Lambda の route 表を読めません"
    return routes


def decision_routes_and_patterns() -> tuple[list[str], list[str]]:
    """決裁 Lambda に向く route と、その呼び出し許可の source_arn（stage の後ろ）。"""
    main = (INFRA / "modules/decision/main.tf").read_text()
    (paths,) = re.findall(r"decision_record_paths = \{(.*?)\n  \}\n", main, re.DOTALL)
    record_paths = re.findall(r'method\s*=\s*"([A-Z]+)"\s*\n\s*path\s*=\s*"([^"]+)"', paths)
    (routes_block,) = re.findall(r"routes = concat\((.*?)\n  \)\n", main, re.DOTALL)
    routes = re.findall(r'^\s*"([A-Z]+ /api/[^"]+)",$', routes_block, re.MULTILINE)
    routes += [f"{method} /api/records/{path}" for method, path in record_paths]
    patterns = []
    for source_arn in re.findall(r'source_arn\s*=\s*"\$\{[^}]+\}/\*/(.+)"$', main, re.MULTILINE):
        if "each.value.path" in source_arn:
            patterns += [
                source_arn.split("${")[0] + path.replace("{recordId}", "*")
                for _, path in record_paths
            ]
        else:
            patterns.append(source_arn)
    assert routes and patterns, "決裁 Lambda の route と呼び出し許可を読めません"
    return routes, patterns


def management_patterns(routes: list[str]) -> list[str]:
    """modules/management_routes の local.invoke_paths と同じ規則で source_arn を作る。"""
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
    def test_management_permissions_match_only_the_known_decision_routes(self):
        decision_routes, _ = decision_routes_and_patterns()
        overlap = matched(management_patterns(management_routes()), decision_routes)
        self.assertEqual(
            overlap,
            {
                "GET /api/records/publication",
                "GET /api/records/{recordId}/publication",
                "GET /api/records/{recordId}/recovery",
            },
        )

    def test_decision_permissions_match_no_management_route(self):
        _, patterns = decision_routes_and_patterns()
        self.assertEqual(matched(patterns, management_routes()), set())

    def test_each_lambda_permission_covers_its_own_routes(self):
        decision_routes, decision_patterns = decision_routes_and_patterns()
        routes = management_routes()
        self.assertEqual(matched(management_patterns(routes), routes), set(routes))
        self.assertEqual(matched(decision_patterns, decision_routes), set(decision_routes))


if __name__ == "__main__":
    unittest.main()
