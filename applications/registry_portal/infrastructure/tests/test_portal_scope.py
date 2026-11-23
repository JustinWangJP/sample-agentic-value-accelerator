"""ポータル用のカスタム scope を本体スタックの 1 か所だけに定義し、ほかは受け取って使うことを確かめる。

追加スタックは別 state（ADR-0013）のため、本体の output portal_api_scope を tfvars に転記して受け取る。
SPA は配信時に本体の output frontend_config（VITE_COGNITO_SCOPES）から受け取り、既定値を持たない。
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


def terraform_sources() -> list[Path]:
    """テストと .terraform を除く Terraform の定義。"""
    return [
        path
        for path in INFRA.rglob("*.tf")
        if ".terraform" not in path.parts and "tests" not in path.parts
    ]


class PortalScopeTests(unittest.TestCase):
    def test_scope_value_is_written_only_in_examples(self):
        scope = portal_api_scope()
        sources = terraform_sources() + [ROOT / "frontend/src/config.ts"]
        for path in sources:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(scope, path.read_text())

    def test_main_stack_outputs_the_scope_for_addon_stacks_and_the_spa(self):
        outputs = (INFRA / "outputs.tf").read_text()
        self.assertRegex(
            outputs, r'output "portal_api_scope" \{[^}]*value\s*=\s*local\.portal_api_scope\n'
        )
        self.assertIn('VITE_COGNITO_SCOPES    = "openid ${local.portal_api_scope}"', outputs)
        main = (INFRA / "rest_api.tf").read_text()
        self.assertRegex(
            main,
            r'module "management_routes" \{[^}]*authorization_scope\s*=\s*local\.portal_api_scope\n',
        )

    def test_addon_stack_routes_require_the_transcribed_scope(self):
        modules = INFRA / "modules"
        self.assertRegex(
            (modules / "decision/main.tf").read_text(),
            r"authorization_scope\s*=\s*var\.portal_api_scope\n",
        )
        self.assertIn(
            "authorization_scopes = [var.authorization_scope]",
            (modules / "management_routes/main.tf").read_text(),
        )
        for stack, pattern in [
            ("decision", r"portal_api_scope\s*=\s*var\.portal_api_scope\n"),
            ("management", r"authorization_scope\s*=\s*var\.portal_api_scope\n"),
        ]:
            with self.subTest(stack=stack):
                self.assertRegex((INFRA / stack / "main.tf").read_text(), pattern)
                self.assertRegex(
                    (INFRA / stack / "variables.tf").read_text(), r'variable "portal_api_scope" \{'
                )

    def test_examples_use_the_scope_defined_by_the_main_stack(self):
        scope = portal_api_scope()
        for stack in ("decision", "management"):
            with self.subTest(stack=stack):
                example = (INFRA / stack / "terraform.tfvars.example").read_text()
                self.assertRegex(example, rf'portal_api_scope\s*=\s*"{re.escape(scope)}"\n')
        example = (ROOT / "frontend/.env.example").read_text()
        self.assertIn(f"VITE_COGNITO_SCOPES=openid {scope}\n", example)


if __name__ == "__main__":
    unittest.main()
