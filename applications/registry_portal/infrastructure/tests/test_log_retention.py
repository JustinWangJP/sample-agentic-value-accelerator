"""各スタックの log_retention_in_days が、CloudWatch Logs の許容値の一覧で検証されることを確かめる。

許容値の一覧は modules/lambda_log_group/retention_days.json の 1 か所に置き、スタックの変数と
modules/lambda_log_group の変数が同じ一覧を読む。本体スタックの不正な値の拒否は portal.tftest.hcl の
log_retention_rejects_unsupported_value で確かめる。decision と notification_events のルートスタックは
mock provider のテストがないため、ここで変数の検証が同じ一覧を読んでいることを確かめる。
"""

from pathlib import Path
import json
import re
import unittest

INFRA = Path(__file__).resolve().parents[1]
RETENTION_DAYS = INFRA / "modules/lambda_log_group/retention_days.json"
STACKS = {
    "本体": INFRA,
    "decision": INFRA / "decision",
    "notification_events": INFRA / "notification_events",
}


def variable_block(stack: Path, name: str) -> str:
    text = (stack / "variables.tf").read_text()
    match = re.search(rf'^variable "{name}" \{{\n(.*?)^\}}', text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{stack}/variables.tf に {name} がありません"
    return match.group(1)


class LogRetentionTests(unittest.TestCase):
    def test_allowed_values_are_the_cloudwatch_logs_retention_days(self):
        # CloudWatch Logs の PutRetentionPolicy が受け付ける値
        # https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutRetentionPolicy.html
        days = json.loads(RETENTION_DAYS.read_text())
        self.assertIn(14, days)
        self.assertIn(365, days)
        self.assertNotIn(10, days)
        self.assertEqual(days, sorted(set(days)))

    def test_every_stack_validates_against_the_shared_list(self):
        for label, stack in STACKS.items():
            with self.subTest(stack=label):
                block = variable_block(stack, "log_retention_in_days")
                self.assertRegex(block, r"default\s*=\s*365\n")
                # condition の file() が共通の一覧を指すこと（パスはスタックの位置から解決する）
                condition = re.search(r"condition\s*=\s*contains\(jsondecode\(file\(\"\$\{path\.module\}/([^\"]+)\"\)\)", block)
                self.assertIsNotNone(condition)
                assert condition is not None
                self.assertEqual((stack / condition.group(1)).resolve(), RETENTION_DAYS)
                self.assertIn("var.log_retention_in_days", block)

    def test_module_validates_against_the_shared_list(self):
        block = variable_block(INFRA / "modules/lambda_log_group", "retention_in_days")
        self.assertIn('contains(jsondecode(file("${path.module}/retention_days.json")), var.retention_in_days)', block)


if __name__ == "__main__":
    unittest.main()
