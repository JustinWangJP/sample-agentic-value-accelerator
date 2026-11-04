from pathlib import Path
import subprocess
import sys
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
            ("pass-role", False),
            ("legacy-registry-action", False),
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


if __name__ == "__main__":
    unittest.main()
