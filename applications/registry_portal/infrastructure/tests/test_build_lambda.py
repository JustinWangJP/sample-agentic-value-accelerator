import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FAKE_PIP = """from pathlib import Path
import sys

args = sys.argv[1:]
target = Path(args[args.index("--target") + 1])
requirements = Path(args[args.index("-r") + 1])
for line in requirements.read_text().splitlines():
    package, version = line.split("==")
    metadata = target / f"{package}-{version}.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(f"Name: {package}\\nVersion: {version}\\n")
for service in ("agent-registry", "agent-registry-control"):
    model = target / "botocore/data" / service / "service-2.json"
    model.parent.mkdir(parents=True)
    model.write_text("{}")
"""


LAMBDA_HANDLERS = ("management", "curation", "events")


def make_project(root: Path, handlers=LAMBDA_HANDLERS, declared=LAMBDA_HANDLERS) -> None:
    """build_lambda.py を、偽の pip と最小のアプリケーションを持つ作業ディレクトリへ置く。

    handlers は実在させる handler モジュール、declared は Terraform の Lambda 定義に書く handler。
    """
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/build_lambda.py", root / "scripts/build_lambda.py")
    package = root / "backend/src/registry_portal"
    (package / "handlers").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    for name in ("__init__", *handlers):
        (package / "handlers" / f"{name}.py").write_text("")
    for name in declared:
        definition = root / "infrastructure/modules" / name / "main.tf"
        definition.parent.mkdir(parents=True)
        definition.write_text(
            f'resource "aws_lambda_function" "{name}" {{\n'
            f'  handler = "registry_portal.handlers.{name}.handler"\n}}\n'
        )
    (root / "backend/requirements.txt").write_text("boto3==9.8.7\nbotocore==9.8.7\n")
    (root / "pip.py").write_text(FAKE_PIP)


def build(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts/build_lambda.py"), *args],
        env=dict(os.environ, PYTHONPATH=str(root)),
        capture_output=True,
        text=True,
    )


class BuildLambdaTests(unittest.TestCase):
    def test_sdk_version_follows_requirements_without_another_version_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_project(root)
            output = root / "build/lambda.zip"
            result = build(root, "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["lambda_zip"], str(output.resolve()))
            with zipfile.ZipFile(output) as archive:
                self.assertIn("boto3-9.8.7.dist-info/METADATA", archive.namelist())
                self.assertIn("botocore-9.8.7.dist-info/METADATA", archive.namelist())
                self.assertIn("registry_portal/__init__.py", archive.namelist())

    def test_default_zip_serves_management_decision_and_event_lambdas(self):
        # decision・notification_events の tfvars 例は ../../backend/build/lambda.zip を指す
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_project(root)
            result = build(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = root / "backend/build/lambda.zip"
            self.assertEqual(json.loads(result.stdout)["lambda_zip"], str(output.resolve()))
            with zipfile.ZipFile(output) as archive:
                for name in LAMBDA_HANDLERS:
                    self.assertIn(f"registry_portal/handlers/{name}.py", archive.namelist())

    def test_build_stops_when_a_lambda_handler_module_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_project(root, handlers=("management", "curation"))
            result = build(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("registry_portal.handlers.events", result.stderr)
            self.assertFalse((root / "backend/build/lambda.zip").exists())

    def test_required_handlers_follow_the_terraform_lambda_definitions(self):
        # handler の一覧は Terraform の Lambda 定義だけに書き、ビルド側で別に持たない
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_project(root, declared=(*LAMBDA_HANDLERS, "reports"))
            result = build(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("registry_portal.handlers.reports", result.stderr)

    def test_build_stops_when_no_lambda_definition_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_project(root, declared=())
            result = build(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "backend/build/lambda.zip").exists())

    def test_repository_lambda_definitions_name_the_three_shared_handlers(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import build_lambda
        finally:
            sys.path.pop(0)
        self.assertEqual(
            build_lambda.lambda_handler_modules(ROOT),
            [f"registry_portal.handlers.{name}" for name in sorted(LAMBDA_HANDLERS)],
        )


if __name__ == "__main__":
    unittest.main()
