import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
# 追加スタックのポリシーテンプレートに埋める値（plan で確定している想定）
TEMPLATE_VALUES = {
    "registry_arn": "arn:aws:agent-registry:us-east-1:123456789012:registry/portal",
    "supplement_table_arn": (
        "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-supplement"
    ),
    "discovery_secret_arn": (
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
    ),
    "log_group_arn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/portal",
    "dead_letter_queue_arn": "arn:aws:sqs:us-east-1:123456789012:registry-portal-events-dlq",
}


def run_guard(plan_path):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_plan.py"), str(plan_path)],
        capture_output=True,
        text=True,
    )


class PlanGuardTests(unittest.TestCase):
    def test_registry_update_is_rejected(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/check_plan.py"),
                str(FIXTURES / "registry-update.json"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("既存 Registry", result.stderr)

    def test_saved_plans_enforce_permission_and_resource_boundaries(self):
        for fixture, allowed in [
            ("safe", True),
            ("registry-delete", False),
            ("registry-create", False),
            ("function-url", False),
            ("forbidden-policy", False),
            ("wildcard-policy", False),
            ("not-action-policy", False),
            ("unknown-policy", False),
            ("malformed", False),
            ("before-registry-arn", False),
            ("managed-attachment", False),
            ("unknown-managed-policy", False),
            ("pass-role", False),
            ("legacy-registry-action", False),
            ("submit-registry-approval", False),
            ("legacy-submit-registry-approval", False),
        ]:
            with self.subTest(fixture=fixture):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts/check_plan.py"),
                        str(FIXTURES / f"{fixture}.json"),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode, 0 if allowed else 1, result.stdout + result.stderr
                )

    def test_registry_write_actions_are_allowed_only_for_their_own_role(self):
        # 管理ロール（management_drafts）は下書きの作成・更新・申請、
        # 決裁ロール（decision）は状態遷移だけを、それぞれの inline policy に限って許可する
        for fixture, allowed in [
            ("management-drafts", True),
            ("decision-status", True),
            # 決裁ロールに descriptor の作成・更新・申請は与えない
            ("decision-drafts", False),
            ("decision-submit", False),
            # 管理ロールにも状態遷移・削除・ワイルドカード・旧 prefix は与えない
            ("management-status", False),
            ("management-delete", False),
            ("management-wildcard", False),
            ("management-create-wildcard", False),
            ("management-legacy-prefix", False),
            # イベント処理ロールには引き続き禁止する
            ("events-status", False),
            ("events-drafts", False),
            # 同じ名前でも、任意の主体に付けられる aws_iam_policy は対象外
            ("management-drafts-managed-policy", False),
        ]:
            with self.subTest(fixture=fixture):
                result = run_guard(FIXTURES / f"{fixture}.json")
                self.assertEqual(
                    result.returncode, 0 if allowed else 1, result.stdout + result.stderr
                )
                if not allowed:
                    self.assertIn("禁止 action", result.stderr)

    def test_additional_stack_policy_templates_pass_the_guard(self):
        # 追加スタックの実際のポリシー本文を、各スタックの plan と同じ資源アドレスで検査する。
        # テンプレートに他ロールの action を足すと、ここで中止される
        modules = ROOT / "infrastructure/modules"
        for address, template in [
            (
                "module.management_drafts.aws_iam_role_policy.management_drafts",
                modules / "management_drafts/draft-policy.json.tftpl",
            ),
            (
                "module.decision.aws_iam_role_policy.decision",
                modules / "decision/decision-policy.json.tftpl",
            ),
            (
                "module.notification_events.aws_iam_role_policy.events",
                modules / "notification_events/events-policy.json.tftpl",
            ),
        ]:
            with self.subTest(address=address):
                policy = re.sub(
                    r"\$\{(\w+)\}",
                    lambda match: TEMPLATE_VALUES[match.group(1)],
                    template.read_text(),
                )
                resource = {
                    "address": address,
                    "mode": "managed",
                    "type": "aws_iam_role_policy",
                    "values": {"role": "registry-portal", "policy": policy},
                }
                plan = {
                    "format_version": "1.2",
                    "resource_changes": [],
                    "planned_values": {"root_module": {"resources": [resource]}},
                }
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as saved:
                    json.dump(plan, saved)
                    saved.flush()
                    result = run_guard(saved.name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_provider_managed_policy_grant_is_rejected(self):
        fixture = json.loads((FIXTURES / "managed-policy-paths.json").read_text())
        for resource in fixture["planned_values"]["root_module"]["resources"]:
            for source in ("planned_values", "resource_changes"):
                with self.subTest(kind=resource["type"], source=source):
                    plan = {
                        "format_version": "1.2",
                        "resource_changes": [],
                        "planned_values": {"root_module": {"resources": []}},
                    }
                    if source == "planned_values":
                        plan["planned_values"]["root_module"]["resources"] = [resource]
                    else:
                        change = dict(resource, change={"after": resource["values"]})
                        plan["resource_changes"] = [change]
                    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as saved:
                        json.dump(plan, saved)
                        saved.flush()
                        result = subprocess.run(
                            [sys.executable, str(ROOT / "scripts/check_plan.py"), saved.name],
                            capture_output=True,
                            text=True,
                        )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("managed policy", result.stderr)


if __name__ == "__main__":
    unittest.main()
