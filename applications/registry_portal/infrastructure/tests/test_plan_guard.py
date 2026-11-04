import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


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
