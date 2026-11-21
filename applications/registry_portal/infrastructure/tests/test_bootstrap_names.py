"""bootstrap が permission set の Resource に使う本体スタックの名前が、本体の定義と一致することを確かめる。

bootstrap は本体スタックより先に適用するため、本体の資源を参照できず名前から ARN を組み立てる。
本体の資源を改名したときに bootstrap が追従しないと、配信用の権限が実際の資源に届かなくなる。
"""

from pathlib import Path
import re
import unittest

INFRA = Path(__file__).resolve().parents[1]


def bootstrap_main_stack_names() -> dict[str, str]:
    source = (INFRA / "bootstrap/main.tf").read_text()
    block = re.search(r"main_stack = \{(.*?)\n  \}", source, re.DOTALL)
    assert block, "bootstrap/main.tf に local.main_stack がありません"
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]+)"', block.group(1), re.MULTILINE))


def main_stack_value(pattern: str) -> str:
    source = (INFRA / "main.tf").read_text()
    match = re.search(pattern, source, re.DOTALL)
    assert match, f"main.tf に {pattern} がありません"
    return match.group(1)


class BootstrapNameTests(unittest.TestCase):
    def test_bootstrap_builds_resource_names_with_the_main_stack_rules(self):
        names = bootstrap_main_stack_names()
        expected = {
            "management_name": main_stack_value(r'management_name = "([^"]+)"'),
            "auxiliary_table": main_stack_value(
                r'resource "aws_dynamodb_table" "auxiliary" \{\s*name\s*=\s*"([^"]+)"'
            ),
            "api_log_group": main_stack_value(
                r'resource "aws_cloudwatch_log_group" "api" \{\s*name\s*=\s*"([^"]+)"'
            ),
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(names.get(key), value)
        # 管理 Lambda のロググループは共通のモジュールが /aws/lambda/<関数名> で作る
        self.assertEqual(
            main_stack_value(
                r'module "management_log_group" \{\s*source\s*=\s*"\./modules/lambda_log_group"'
                r"\s*function_name\s*=\s*([^\n]+)\n"
            ),
            "local.management_name",
        )
        log_group_module = (INFRA / "modules/lambda_log_group/main.tf").read_text()
        self.assertIn('name              = "/aws/lambda/${var.function_name}"', log_group_module)

    def test_bootstrap_state_key_matches_the_main_stack_backend(self):
        backend = (INFRA / "backend.hcl.example").read_text()
        key = re.search(r'^key\s*=\s*"([^"]+)"', backend, re.MULTILINE).group(1)
        self.assertEqual(bootstrap_main_stack_names().get("state_key"), key)


if __name__ == "__main__":
    unittest.main()
