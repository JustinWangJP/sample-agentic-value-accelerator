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
FAKE_PIP = '''from pathlib import Path
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
'''


class BuildLambdaTests(unittest.TestCase):
    def test_sdk_version_follows_requirements_without_another_version_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts/build_lambda.py", root / "scripts/build_lambda.py")
            source = root / "backend/src/registry_portal"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("")
            (root / "backend/requirements.txt").write_text("boto3==9.8.7\nbotocore==9.8.7\n")
            (root / "pip.py").write_text(FAKE_PIP)
            output = root / "build/management.zip"
            result = subprocess.run(
                [sys.executable, str(root / "scripts/build_lambda.py"), "--output", str(output)],
                env=dict(os.environ, PYTHONPATH=str(root)),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["lambda_zip"], str(output.resolve()))
            with zipfile.ZipFile(output) as archive:
                self.assertIn("boto3-9.8.7.dist-info/METADATA", archive.namelist())
                self.assertIn("botocore-9.8.7.dist-info/METADATA", archive.namelist())
                self.assertIn("registry_portal/__init__.py", archive.namelist())


if __name__ == "__main__":
    unittest.main()
