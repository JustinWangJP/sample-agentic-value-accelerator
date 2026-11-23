"""追加スタック（management・decision・notification_events）が本体スタックとそろっていることを検査する。"""

from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infrastructure"
ADDON_STACKS = ("management", "decision", "notification_events")
AWS_PROVIDER_VERSION = re.compile(
    r'aws\s*=\s*\{[^}]*source\s*=\s*"hashicorp/aws"[^}]*version\s*=\s*"([^"]+)"', re.S
)


def aws_provider_version(stack_dir: Path) -> str:
    match = AWS_PROVIDER_VERSION.search((stack_dir / "versions.tf").read_text())
    if not match:
        raise AssertionError(f"AWS provider のバージョン指定がありません: {stack_dir}")
    return match.group(1)


def backend_settings(path: Path) -> dict[str, str]:
    settings = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            settings[name.strip()] = value.strip().strip('"')
    return settings


class AddonStackTests(unittest.TestCase):
    def test_addon_stacks_pin_the_same_aws_provider_as_the_main_stack(self):
        self.assertEqual(aws_provider_version(INFRA), "= 6.71.0")
        for stack in ADDON_STACKS:
            with self.subTest(stack=stack):
                self.assertEqual(aws_provider_version(INFRA / stack), "= 6.71.0")

    def test_backend_examples_keep_every_state_under_the_dev_prefix(self):
        # key はガイド 1.4 の表どおり。本体と同じバケットで key だけを分ける
        expected = {
            ".": "registry-portal/dev/terraform.tfstate",
            "management": "registry-portal/dev/management.tfstate",
            "decision": "registry-portal/dev/decision.tfstate",
            "notification_events": "registry-portal/dev/notification-events.tfstate",
        }
        for stack, key in expected.items():
            with self.subTest(stack=stack):
                settings = backend_settings(INFRA / stack / "backend.hcl.example")
                self.assertEqual(settings["key"], key)
                self.assertEqual(settings["region"], "us-east-1")
                self.assertEqual(settings["encrypt"], "true")
                self.assertEqual(settings["use_lockfile"], "true")

    def test_ci_validates_every_root_stack_without_a_backend(self):
        workflow = (ROOT.parents[1] / ".github/workflows/registry-portal-ci.yml").read_text()
        stacks = sorted(
            path.parent.name
            for path in INFRA.glob("*/versions.tf")
            if path.parent.name not in ("modules", "tests")
        )
        self.assertIn("notification_events", stacks)
        for stack in stacks:
            with self.subTest(stack=stack):
                self.assertIn(
                    f"terraform -chdir=infrastructure/{stack} init -backend=false", workflow
                )
                self.assertIn(f"terraform -chdir=infrastructure/{stack} validate", workflow)

    def test_tfvars_examples_shorten_log_retention_for_dev(self):
        # dev.tfvars と追加スタックの tfvars は各例をコピーして作る（ガイド 4.3）。
        # ロググループを持つスタックの例には、dev の保持日数（14 日）を有効な行で書く（#62）
        for stack in (".", "decision", "notification_events"):
            with self.subTest(stack=stack):
                example = (INFRA / stack / "terraform.tfvars.example").read_text()
                self.assertRegex(example, r"(?m)^log_retention_in_days\s*=\s*14\s*$")

    def test_local_backend_settings_and_lambda_zip_are_not_committed(self):
        paths = [
            "backend/build/lambda.zip",
            "infrastructure/backend.hcl",
            *(f"infrastructure/{stack}/backend.hcl" for stack in ADDON_STACKS),
        ]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", *paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(sorted(result.stdout.split()), sorted(paths), result.stderr)
        examples = [f"infrastructure/{stack}/backend.hcl.example" for stack in ADDON_STACKS]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", *examples],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
