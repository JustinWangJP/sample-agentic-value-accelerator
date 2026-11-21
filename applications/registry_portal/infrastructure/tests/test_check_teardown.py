"""scripts/check_teardown.py（deploy.sh --destroy が Terraform を反映する前に行う検査）のテスト。"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from teardown_helpers import LOG_GROUP, pulled_state

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/check_teardown.py"
FIXTURES = Path(__file__).parent / "fixtures"


def run_check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True)


class AddonStatesTests(unittest.TestCase):
    """本体を撤去する前に、追加スタックの state にリソースが残っていないことを確かめる。"""

    def check_states(self, states: dict[str, dict | str]) -> subprocess.CompletedProcess[str]:
        """state を一時ファイルに書いて検査する。文字列は state pull の出力としてそのまま書く。"""
        with tempfile.TemporaryDirectory() as directory:
            arguments = []
            for name, state in states.items():
                path = Path(directory) / f"{name}.json"
                path.write_text(state if isinstance(state, str) else json.dumps(state))
                arguments.append(f"{name}={path}")
            return run_check("addon-states", *arguments)

    def test_remaining_resources_stop_with_the_stack_and_count(self):
        in_module = dict(LOG_GROUP, module="module.logs")
        result = self.check_states(
            {"management": pulled_state(LOG_GROUP, in_module), "decision": pulled_state()}
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("management（2 件）", result.stderr)
        self.assertNotIn("decision", result.stderr)
        self.assertIn("追加スタック → 本体", result.stderr)

    def test_data_sources_and_resources_without_instances_are_not_counted(self):
        data = {"mode": "data", "type": "aws_caller_identity", "name": "current", "instances": [{}]}
        emptied = dict(LOG_GROUP, instances=[])
        result = self.check_states({"decision": pulled_state(data, emptied)})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_state_and_empty_state_both_pass_but_are_reported_differently(self):
        # state のファイルがないと、terraform state pull は何も出力しない（Terraform 1.17.1 で確認）
        result = self.check_states({"decision": "", "management": pulled_state()})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("decision: state なし（未配信）", result.stdout)
        self.assertIn("management: 0 件", result.stdout)

    def test_unreadable_state_stops_instead_of_counting_as_empty(self):
        for label, text in {
            "JSON の破損": "{",
            "想定外の形式（version）": json.dumps({"version": 3, "resources": []}),
            "resources がない": json.dumps({"version": 4}),
        }.items():
            with self.subTest(label):
                result = self.check_states({"decision": text})
                self.assertEqual(result.returncode, 1)
                self.assertIn("decision", result.stderr)
                self.assertIn("読めません", result.stderr)

    def test_malformed_arguments_stop_as_usage_errors_before_reading_states(self):
        # `=` がない、スタック名やパスが空の引数は、どの state も読まずに使い方の誤りとして止める
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "decision.json"
            valid.write_text(json.dumps(pulled_state()))
            for argument in ("management", "=state.json", "management=", "="):
                with self.subTest(argument=argument):
                    result = run_check("addon-states", f"decision={valid}", argument)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(f"「{argument}」", result.stderr)
                    self.assertIn("<スタック名>=<state pull の出力>", result.stderr)
                    self.assertNotIn("decision:", result.stdout)


MAIN_BACKEND = """bucket       = "registry-portal-state-123456789012"
key          = "registry-portal/dev/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
"""


class AddonBackendsTests(unittest.TestCase):
    """本体の撤去で追加スタックの state を読む前に、追加スタックの backend.hcl のバケットを確かめる。"""

    def check_backends(self, addons: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
        """本体と追加スタックの backend.hcl を書いて検査する。None はファイルを作らない。"""
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / "backend.hcl"
            main.write_text(MAIN_BACKEND)
            arguments = []
            for stack, text in addons.items():
                path = Path(directory) / stack / "backend.hcl"
                if text is not None:
                    path.parent.mkdir()
                    path.write_text(text)
                arguments.append(f"{stack}={path}")
            return run_check("addon-backends", str(main), *arguments)

    def test_same_bucket_and_region_with_another_key_passes(self):
        addon = "# 本体と同じバケットで key だけを分ける\n" + MAIN_BACKEND.replace(
            "terraform.tfstate", "decision.tfstate"
        )
        result = self.check_backends({"decision": addon})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "decision: backend.hcl のバケットとリージョンが本体と一致しました", result.stdout
        )

    def test_another_bucket_or_region_stops_before_reading_states(self):
        cases = {
            "別のバケット": MAIN_BACKEND.replace("123456789012", "210987654321"),
            "別のリージョン": MAIN_BACKEND.replace("us-east-1", "ap-northeast-1"),
            "リージョンの指定なし": MAIN_BACKEND.replace('region       = "us-east-1"\n', ""),
        }
        for label, text in cases.items():
            with self.subTest(label):
                result = self.check_backends({"decision": MAIN_BACKEND, "management": text})
                self.assertEqual(result.returncode, 1)
                self.assertIn("management", result.stderr)
                self.assertIn("本体の backend.hcl と一致しません", result.stderr)
                self.assertNotIn("decision", result.stderr)

    def test_missing_addon_backend_is_reported_with_the_assumption(self):
        result = self.check_backends({"notification_events": None})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("notification_events: ", result.stdout)
        self.assertIn("がないため、本体の backend.hcl のバケットとリージョンで", result.stdout)

    def test_main_backend_without_bucket_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / "backend.hcl"
            main.write_text('key = "registry-portal/dev/terraform.tfstate"\n')
            result = run_check("addon-backends", str(main), f"decision={directory}/none.hcl")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bucket", result.stderr)

    def test_malformed_arguments_stop_as_usage_errors(self):
        result = run_check("addon-backends", "backend.hcl", "decision")
        self.assertEqual(result.returncode, 2)
        self.assertIn("<スタック名>=<backend.hcl>", result.stderr)


class MainProtectionTests(unittest.TestCase):
    """本体の destroy の plan で、補助テーブルと SPA バケットの保護が state 上で外れていることを確かめる。"""

    def check_plan(self, plan: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(plan))
            return run_check("main-protection", str(path))

    def destroy_plan(self, **before_changes) -> dict:
        """保護を外して apply した後の destroy の plan に、削除前の値の変更を加えて返す。"""
        plan = json.loads((FIXTURES / "destroy-main.json").read_text())
        for change in plan["resource_changes"]:
            for address, values in before_changes.items():
                if change["address"] == address:
                    change["change"]["before"].update(values)
        return plan

    def test_plan_after_the_unprotecting_apply_passes(self):
        result = self.check_plan(self.destroy_plan())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_protection_still_in_state_stops_with_the_guide_step(self):
        result = run_check("main-protection", str(FIXTURES / "destroy-main-protected.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("aws_dynamodb_table.auxiliary", result.stderr)
        self.assertIn("deletion_protection_enabled", result.stderr)
        self.assertIn("aws_s3_bucket.spa", result.stderr)
        self.assertIn("force_destroy", result.stderr)
        self.assertIn("5.4", result.stderr)

    def test_each_protection_is_checked_on_its_own(self):
        cases = {
            "aws_dynamodb_table.auxiliary": {"deletion_protection_enabled": True},
            "aws_s3_bucket.spa": {"force_destroy": False},
        }
        for address, values in cases.items():
            with self.subTest(address):
                result = self.check_plan(self.destroy_plan(**{address: values}))
                self.assertEqual(result.returncode, 1)
                self.assertIn(address, result.stderr)
                (other,) = cases.keys() - {address}
                self.assertNotIn(other, result.stderr)

    def test_unknown_protection_value_stops(self):
        # 値が bool でない（属性がない、null）場合は、保護が外れていると確かめられないため止める
        for value in (None, "false"):
            with self.subTest(value=value):
                plan = self.destroy_plan(
                    **{"aws_dynamodb_table.auxiliary": {"deletion_protection_enabled": value}}
                )
                result = self.check_plan(plan)
                self.assertEqual(result.returncode, 1)
                self.assertIn("aws_dynamodb_table.auxiliary", result.stderr)

    def test_already_deleted_resources_are_not_required(self):
        # 撤去が途中で失敗した後の再実行では、削除済みのリソースは plan に出ない
        plan = self.destroy_plan()
        plan["resource_changes"] = [
            change
            for change in plan["resource_changes"]
            if change["address"] != "aws_s3_bucket.spa"
        ]
        result = self.check_plan(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        # リソース名の変更でも plan に出なくなるため、飛ばした検査を表示して気づけるようにする
        self.assertIn("aws_s3_bucket.spa は plan にないため", result.stdout)
        self.assertIn("force_destroy の検査を飛ばしました", result.stdout)
        self.assertNotIn("aws_dynamodb_table.auxiliary", result.stdout)

    def test_unreadable_plan_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text("{")
            result = run_check("main-protection", str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("読めません", result.stderr)


if __name__ == "__main__":
    unittest.main()
