import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FAKE = """#!PYTHON
import json, os
from pathlib import Path
import subprocess, sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "python3":
    if args and args[0].endswith("build_lambda.py"):
        Path(args[args.index("--output") + 1]).write_bytes(b"test zip")
    else:
        sys.exit(subprocess.run(["PYTHON", *args]).returncode)
elif name == "aws":
    if args[:3] == ["configure", "get", "sso_session"]:
        print("portal-session")
    if args[:2] == ["sts", "get-caller-identity"]:
        print("arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_RegistryPortalDeployer_abc/operator")
elif name == "terraform":
    if "show" in args and "-json" in args:
        print(Path(os.environ["PLAN_FIXTURE"]).read_text())
    elif "output" in args:
        print(json.dumps({"spa_bucket_name": {"value": "portal-spa"},
                          "cloudfront_distribution_id": {"value": "EDIST"},
                          "frontend_config": {"value": {
                              "VITE_COGNITO_SCOPES": "openid registry-portal/catalog.read"}}}))
"""


class DeployTests(unittest.TestCase):
    def run_deploy(self, fixture="safe", confirmation="apply\n", ci=""):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            commands = work / "commands.jsonl"
            commands.touch()
            for name in ("python3", "terraform", "aws", "npm"):
                executable = work / name
                executable.write_text(FAKE.replace("PYTHON", sys.executable))
                executable.chmod(0o755)
            backend = work / "backend.hcl"
            variables = work / "dev.tfvars"
            backend.touch()
            variables.touch()
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AWS_") and key not in {"CI", "GITHUB_ACTIONS"}
            }
            env.update(
                PATH=f"{work}:{os.environ['PATH']}",
                AWS_PROFILE="portal",
                CI=ci,
                COMMAND_LOG=str(commands),
                PLAN_FIXTURE=str(Path(__file__).parent / f"fixtures/{fixture}.json"),
            )
            result = subprocess.run(
                [str(ROOT / "scripts/deploy.sh"), str(backend), str(variables)],
                env=env,
                input=confirmation,
                text=True,
                capture_output=True,
            )
            return result, [json.loads(line) for line in commands.read_text().splitlines()]

    def test_approved_saved_plan_is_applied_then_spa_is_uploaded(self):
        result, commands = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        operations = [
            (
                command[0],
                next(
                    (
                        item
                        for item in command[1:]
                        if item in {"plan", "apply", "sync", "cp", "create-invalidation"}
                    ),
                    "",
                ),
            )
            for command in commands
        ]
        sequence = [operation for operation in operations if operation[1]]
        self.assertEqual(
            sequence,
            [
                ("terraform", "plan"),
                ("terraform", "apply"),
                ("aws", "sync"),
                ("aws", "cp"),
                ("aws", "create-invalidation"),
            ],
        )
        plan = next(command for command in commands if "plan" in command)
        apply = next(command for command in commands if "apply" in command)
        self.assertEqual(next(item[5:] for item in plan if item.startswith("-out=")), apply[-1])
        init = next(command for command in commands if "init" in command)
        self.assertIn("-backend-config=encrypt=true", init)
        self.assertIn("-backend-config=use_lockfile=true", init)

    def test_guard_failure_or_cancel_never_reaches_apply_or_upload(self):
        for fixture, confirmation in [("registry-update", "apply\n"), ("safe", "no\n")]:
            with self.subTest(fixture=fixture):
                result, commands = self.run_deploy(fixture, confirmation)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("apply" in command for command in commands))
                self.assertFalse(
                    any("s3" in command or "cloudfront" in command for command in commands)
                )

    def test_ci_is_rejected_before_external_commands(self):
        result, commands = self.run_deploy(ci="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CI", result.stderr)
        self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
