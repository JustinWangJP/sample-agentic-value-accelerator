"""ポータル用のカスタム scope が、Terraform の各スタックと SPA の既定値でそろっていることを確かめる。

追加スタックは別 state（ADR-0013）のため本体の local を参照できず、SPA は TypeScript で持つ。
どこか 1 か所だけ改名すると、route の JWT 検証でトークンが拒否される。
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infrastructure"


def portal_api_scope() -> str:
    main = (INFRA / "main.tf").read_text()
    identifier = re.search(
        r'resource "aws_cognito_resource_server" "portal" \{\s*identifier\s*=\s*"([^"]+)"', main
    )
    scope_name = re.search(r'portal_api_scope_name\s*=\s*"([^"]+)"', main)
    assert identifier and scope_name, "main.tf からポータル用の scope を読めません"
    return f"{identifier.group(1)}/{scope_name.group(1)}"


class PortalScopeTests(unittest.TestCase):
    def test_addon_stack_routes_require_the_scope_defined_by_the_main_stack(self):
        scope = portal_api_scope()
        for module in ("decision", "management_routes"):
            with self.subTest(module=module):
                source = (INFRA / "modules" / module / "main.tf").read_text()
                scopes = re.findall(r'authorization_scopes\s*=\s*\["([^"]+)"\]', source)
                self.assertTrue(scopes)
                self.assertEqual(set(scopes), {scope})

    def test_spa_default_scope_matches_the_main_stack(self):
        config = (ROOT / "frontend/src/config.ts").read_text()
        constant = re.search(r"export const PORTAL_API_SCOPE = '([^']+)'", config)
        self.assertIsNotNone(constant)
        self.assertEqual(constant.group(1), portal_api_scope())
        example = (ROOT / "frontend/.env.example").read_text()
        self.assertIn(f"VITE_COGNITO_SCOPES=openid {portal_api_scope()}\n", example)


if __name__ == "__main__":
    unittest.main()
