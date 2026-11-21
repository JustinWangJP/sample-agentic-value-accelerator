"""scripts/check_teardown.py（deploy.sh --destroy が Terraform を反映する前に行う検査）のテスト。"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/check_teardown.py"
LOG_GROUP = {"address": "aws_cloudwatch_log_group.this", "mode": "managed"}


def run_check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True)


def show_json(root_module: dict) -> str:
    return json.dumps({"format_version": "1.0", "values": {"root_module": root_module}})


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
        cases = {
            "ルートモジュールに残る": {"resources": [LOG_GROUP, LOG_GROUP]},
            "子モジュールに残る": {
                "child_modules": [{"address": "module.logs", "resources": [LOG_GROUP, LOG_GROUP]}]
            },
        }
        for label, root_module in cases.items():
            with self.subTest(label):
                result = self.check_states(
                    {"management": show_json(root_module), "decision": show_json({})}
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("management（2 件）", result.stderr)
                self.assertNotIn("decision", result.stderr)
                self.assertIn("追加スタック → 本体", result.stderr)

    def test_data_sources_are_not_counted_as_remaining_resources(self):
        data = {"address": "data.aws_caller_identity.current", "mode": "data"}
        result = self.check_states({"decision": show_json({"resources": [data]})})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unreadable_state_stops_instead_of_counting_as_empty(self):
        for label, text in {
            "JSON の破損": "{",
            "format_version がない": json.dumps({"values": {}}),
        }.items():
            with self.subTest(label):
                result = self.check_states({"decision": text})
                self.assertEqual(result.returncode, 1)
                self.assertIn("decision", result.stderr)
                self.assertIn("読めません", result.stderr)


if __name__ == "__main__":
    unittest.main()
