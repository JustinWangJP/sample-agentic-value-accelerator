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


# 本体の output rest_api を転記する変数。Terraform には型の別名がないため、型を 3 か所に書く。
# decision は output のすべての属性を使う。management は使う属性だけを型に含め、
# 転記した output の残りの属性（cors_allowed_origins）は Terraform が型の変換で捨てる
REST_API_VARIABLES = (
    INFRA / "decision/variables.tf",
    INFRA / "modules/decision/variables.tf",
)
MANAGEMENT_REST_API_VARIABLE = INFRA / "management/variables.tf"


def rest_api_type(path: Path) -> dict[str, str]:
    """variable "rest_api" の object 型を、属性名 → 型の辞書にする。"""
    match = re.search(
        r'variable "rest_api" \{\s*type\s*=\s*object\(\{(.*?)\}\)', path.read_text(), re.S
    )
    if not match:
        raise AssertionError(f"variable rest_api の object 型がありません: {path}")
    return dict(re.findall(r"^\s*(\w+)\s*=\s*(\S+)\s*$", match.group(1), re.M))


def rest_api_output_attributes() -> set[str]:
    """本体スタックの output rest_api の属性名。"""
    match = re.search(
        r'output "rest_api" \{.*?value\s*=\s*\{(.*?)\n  \}',
        (INFRA / "outputs.tf").read_text(),
        re.S,
    )
    if not match:
        raise AssertionError("本体スタックに output rest_api がありません。")
    return set(re.findall(r"^\s*(\w+)\s*=", match.group(1), re.M))


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

    def test_rest_api_variables_share_one_type_matching_the_main_output(self):
        # output rest_api をそのまま転記できるよう、decision の型と output の属性をそろえる。
        # 属性を足すときは、本体の outputs.tf と REST_API_VARIABLES のすべてを直す
        expected = rest_api_type(REST_API_VARIABLES[0])
        self.assertEqual(set(expected), rest_api_output_attributes())
        for path in REST_API_VARIABLES[1:]:
            with self.subTest(path=path.relative_to(INFRA)):
                self.assertEqual(rest_api_type(path), expected)

    def test_management_rest_api_type_is_a_subset_of_the_main_output(self):
        # management は使う属性だけを持つ。output にない属性があると転記した値が型に合わない
        management = rest_api_type(MANAGEMENT_REST_API_VARIABLE)
        full = rest_api_type(REST_API_VARIABLES[0])
        self.assertLess(set(management), set(full))
        self.assertEqual(management, {name: full[name] for name in management})
        self.assertNotIn("cors_allowed_origins", management)

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
