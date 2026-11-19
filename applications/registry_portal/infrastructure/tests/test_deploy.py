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
    log.write(json.dumps({"argv": [name, *args], "profile": os.environ.get("AWS_PROFILE")}) + "\\n")
if name == "python3":
    if args and args[0].endswith("build_lambda.py"):
        Path(args[args.index("--output") + 1]).write_bytes(b"test zip")
    else:
        sys.exit(subprocess.run(["PYTHON", *args]).returncode)
elif name == "aws":
    profiles = json.loads(os.environ["FAKE_AWS_PROFILES"])
    profile = args[args.index("--profile") + 1] if "--profile" in args else os.environ.get("AWS_PROFILE")
    settings = profiles.get(profile, {})
    if args[:2] == ["configure", "get"]:
        if args[2] not in settings:
            sys.exit(1)
        print(settings[args[2]])
    if args[:2] == ["sts", "get-caller-identity"]:
        if "caller_arn" not in settings:
            sys.exit(255)
        print(settings["caller_arn"])
elif name == "terraform":
    if "show" in args and "-json" in args:
        print(Path(os.environ["PLAN_FIXTURE"]).read_text())
    elif "output" in args:
        print(json.dumps({"spa_bucket_name": {"value": "portal-spa"},
                          "cloudfront_distribution_id": {"value": "EDIST"},
                          "frontend_config": {"value": {
                              "VITE_COGNITO_SCOPES": "openid registry-portal/catalog.read"}}}))
"""
SSO_PROFILES = {
    "portal": {
        "sso_session": "portal-session",
        "caller_arn": (
            "arn:aws:sts::123456789012:assumed-role/"
            "AWSReservedSSO_RegistryPortalDeployer_abc/operator"
        ),
    }
}
DEPLOYER_SESSION = "arn:aws:sts::123456789012:assumed-role/RegistryPortalDeployer/botocore-session-1"
# 個人アカウント: MFA 付きのロールのプロファイルと、Terraform 用の credential_process プロファイル
ROLE_PROFILES = {
    "registry-deploy": {
        "role_arn": "arn:aws:iam::123456789012:role/RegistryPortalDeployer",
        "mfa_serial": "arn:aws:iam::123456789012:mfa/operator",
        "source_profile": "registry-operator",
        "caller_arn": DEPLOYER_SESSION,
    },
    "registry-deploy-tf": {
        "credential_process": (
            "aws configure export-credentials --profile registry-deploy --format process"
        ),
        "caller_arn": DEPLOYER_SESSION,
    },
}


def with_profile(profiles, name, **changes):
    """プロファイル設定のコピーを返す。値が None の項目は削除する。"""
    updated = {key: dict(value) for key, value in profiles.items()}
    settings = updated.setdefault(name, {})
    for key, value in changes.items():
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    return updated


class DeployTests(unittest.TestCase):
    def run_deploy(
        self,
        fixture="safe",
        confirmation="apply\n",
        ci="",
        profile="portal",
        profiles=SSO_PROFILES,
        extra_env=None,
    ):
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
                AWS_PROFILE=profile,
                CI=ci,
                FAKE_AWS_PROFILES=json.dumps(profiles),
                COMMAND_LOG=str(commands),
                PLAN_FIXTURE=str(Path(__file__).parent / f"fixtures/{fixture}.json"),
            )
            env.update(extra_env or {})
            result = subprocess.run(
                [str(ROOT / "scripts/deploy.sh"), str(backend), str(variables)],
                env=env,
                input=confirmation,
                text=True,
                capture_output=True,
            )
            self.records = [json.loads(line) for line in commands.read_text().splitlines()]
            return result, [record["argv"] for record in self.records]

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

    def test_lambda_zip_is_named_as_the_shared_package(self):
        # 管理・決裁・イベント処理の Lambda が共通で使う ZIP であることがわかる名前で作り、plan に渡す
        result, commands = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        build = next(
            command
            for command in commands
            if command[0] == "python3" and command[1].endswith("build_lambda.py")
        )
        zip_path = build[build.index("--output") + 1]
        self.assertEqual(Path(zip_path).name, "lambda.zip")
        plan = next(command for command in commands if "plan" in command)
        self.assertIn(f"-var=lambda_zip_path={zip_path}", plan)

    def test_guard_failure_or_cancel_never_reaches_apply_or_upload(self):
        for fixture, confirmation in [("registry-update", "apply\n"), ("safe", "no\n")]:
            with self.subTest(fixture=fixture):
                result, commands = self.run_deploy(fixture, confirmation)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("apply" in command for command in commands))
                self.assertFalse(
                    any("s3" in command or "cloudfront" in command for command in commands)
                )

    def test_role_profile_with_mfa_runs_terraform_through_credential_process_profile(self):
        for extra_env, terraform_profile in [
            ({}, "registry-deploy-tf"),
            ({"TERRAFORM_AWS_PROFILE": "custom-tf"}, "custom-tf"),
        ]:
            with self.subTest(terraform_profile=terraform_profile):
                profiles = {
                    **ROLE_PROFILES,
                    terraform_profile: ROLE_PROFILES["registry-deploy-tf"],
                }
                result, commands = self.run_deploy(
                    profile="registry-deploy", profiles=profiles, extra_env=extra_env
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                terraform = [r for r in self.records if r["argv"][0] == "terraform"]
                self.assertTrue(terraform)
                self.assertEqual({r["profile"] for r in terraform}, {terraform_profile})
                first_identity = next(
                    command for command in commands if command[1:3] == ["sts", "get-caller-identity"]
                )
                self.assertIn("registry-deploy", first_identity)
                self.assertLess(
                    commands.index(first_identity),
                    next(i for i, command in enumerate(commands) if command[0] == "terraform"),
                )
                self.assertTrue(any("create-invalidation" in command for command in commands))

    def test_unsafe_profile_or_principal_is_rejected_before_terraform(self):
        other_session = "arn:aws:sts::123456789012:assumed-role/Administrator/operator"
        cases = {
            "MFA のないロールのプロファイル": (
                "registry-deploy",
                with_profile(ROLE_PROFILES, "registry-deploy", mfa_serial=None),
            ),
            "Terraform 用のプロファイルがない": (
                "registry-deploy",
                {"registry-deploy": ROLE_PROFILES["registry-deploy"]},
            ),
            "credential_process が別のプロファイルを参照する": (
                "registry-deploy",
                with_profile(
                    ROLE_PROFILES,
                    "registry-deploy-tf",
                    credential_process=(
                        "aws configure export-credentials --profile registry-admin --format process"
                    ),
                ),
            ),
            "RegistryPortalDeployer 以外のロール": (
                "registry-deploy",
                with_profile(
                    with_profile(ROLE_PROFILES, "registry-deploy", caller_arn=other_session),
                    "registry-deploy-tf",
                    caller_arn=other_session,
                ),
            ),
            "Terraform 用のプロファイルが別の主体になる": (
                "registry-deploy",
                with_profile(ROLE_PROFILES, "registry-deploy-tf", caller_arn=other_session),
            ),
            "credential_process のプロファイルを直接指定する": ("registry-deploy-tf", ROLE_PROFILES),
            "SSO の別の permission set": (
                "portal",
                with_profile(
                    SSO_PROFILES,
                    "portal",
                    caller_arn=(
                        "arn:aws:sts::123456789012:assumed-role/"
                        "AWSReservedSSO_AdministratorAccess_abc/operator"
                    ),
                ),
            ),
        }
        for label, (profile, profiles) in cases.items():
            with self.subTest(label):
                result, commands = self.run_deploy(profile=profile, profiles=profiles)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(command[0] == "terraform" for command in commands))
                self.assertFalse(any("s3" in command for command in commands))

    def test_ci_is_rejected_before_external_commands(self):
        result, commands = self.run_deploy(ci="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CI", result.stderr)
        self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
