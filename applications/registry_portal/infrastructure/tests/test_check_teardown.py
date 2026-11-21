"""scripts/check_teardown.py（deploy.sh --destroy が Terraform を反映する前に行う検査）のテスト。"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/check_teardown.py"
LOG_GROUP = {
    "mode": "managed",
    "type": "aws_cloudwatch_log_group",
    "name": "this",
    "instances": [{"attributes": {}}],
}


def run_check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True)


def pulled_state(*resources: dict) -> str:
    """terraform state pull が出力する state（format version 4）を返す。"""
    return json.dumps({"version": 4, "serial": 3, "lineage": "x", "resources": list(resources)})


class AddonStatesTests(unittest.TestCase):
    """本体を撤去する前に、追加スタックの state にリソースが残っていないことを確かめる。"""

    def check_states(self, states: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            arguments = []
            for name, text in states.items():
                path = Path(directory) / f"{name}.json"
                path.write_text(text)
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


if __name__ == "__main__":
    unittest.main()
