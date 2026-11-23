import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from teardown_helpers import LOG_GROUP, pulled_state

ROOT = Path(__file__).resolve().parents[2]
# スタックごとの backend.hcl の state key（各 backend.hcl.example と同じ値）
STATE_KEYS = {
    "main": "registry-portal/dev/terraform.tfstate",
    "decision": "registry-portal/dev/decision.tfstate",
    "management": "registry-portal/dev/management.tfstate",
    "notification_events": "registry-portal/dev/notification-events.tfstate",
}
ADDON_STACKS = tuple(stack for stack in STATE_KEYS if stack != "main")
FAKE = """#!PYTHON
import json, os
from pathlib import Path
import subprocess, sys
from zipfile import ZipFile
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps({"argv": [name, *args], "profile": os.environ.get("AWS_PROFILE"),
                          "cwd": os.getcwd()}) + "\\n")
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
    if args[:2] == ["apigateway", "create-deployment"]:
        print("deployment123")
elif name == "terraform":
    if "plan" in args:
        plan_path = next(arg[5:] for arg in args if arg.startswith("-out="))
        with ZipFile(plan_path, "w") as plan:
            plan.writestr("tfconfig/modules.json", json.dumps([{"Key": "", "Dir": "."}]))
            plan.writestr("tfconfig/m-/main.tf", "terraform {}")
    elif "show" in args and "-json" in args and args[-1].endswith(".tfplan"):
        plan = json.loads(Path(os.environ["PLAN_FIXTURE"]).read_text())
        stack_dir = args[0].removeprefix("-chdir=") if args[0].startswith("-chdir=") else os.getcwd()
        if Path(stack_dir).name in {"decision", "management"}:
            # 本体の output rest_api の転記。ステージ名は既定値（v1）と区別できる値にする
            plan.setdefault("variables", {}).update(
                rest_api={"value": {"id": "abcde12345", "stage_name": "stage-from-plan"}},
                region={"value": "us-east-1"},
            )
        print(json.dumps(plan))
    elif args[1:3] == ["state", "pull"]:
        # FAKE_STATES にないスタックは state のファイルがない（state pull は何も出力しない）
        stack = Path(args[0].split("=", 1)[1]).name
        if stack == os.environ.get("FAKE_STATE_PULL_FAILS"):
            sys.exit(1)
        states = json.loads(os.environ.get("FAKE_STATES", "{}"))
        if stack in states:
            print(json.dumps(states[stack]))
    elif "output" in args:
        outputs = {"spa_bucket_name": "portal-spa",
                   "cloudfront_distribution_id": "EDIST",
                   "rest_api": {"id": "abcde12345", "stage_name": "stage-from-output"},
                   "api_region": "us-east-1",
                   "frontend_config": {"VITE_COGNITO_SCOPES": "openid registry-portal/api.invoke"}}
        value = outputs[args[-1]]
        print(json.dumps(value) if "-json" in args else value, end="" if "-raw" in args else "\\n")
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
        stack_args=(),
        backend_key=None,
        destroy=False,
        states=None,
        addon_backends=None,
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
            # 既定は指定したスタックの state key。--stack が不正な場合は本体の key を書く
            stack = stack_args[1] if len(stack_args) == 2 else "main"
            key = STATE_KEYS.get(stack, STATE_KEYS["main"]) if backend_key is None else backend_key
            backend.write_text(
                f'bucket = "registry-portal-state-123456789012"\nkey    = "{key}"\n'
                if key
                else 'bucket = "registry-portal-state-123456789012"\n'
            )
            variables.touch()
            # 追加スタックの backend.hcl は、本体の backend.hcl と同じディレクトリの <スタック>/ に置く
            for addon, text in (addon_backends or {}).items():
                (work / addon).mkdir()
                (work / addon / "backend.hcl").write_text(text)
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
                FAKE_STATES=json.dumps(states or {}),
            )
            env.update(extra_env or {})
            result = subprocess.run(
                [
                    str(ROOT / "scripts/deploy.sh"),
                    *(("--destroy",) if destroy else ()),
                    *stack_args,
                    str(backend),
                    str(variables),
                ],
                env=env,
                input=confirmation,
                text=True,
                capture_output=True,
            )
            self.records = [json.loads(line) for line in commands.read_text().splitlines()]
            return result, [record["argv"] for record in self.records]

    def run_destroy(self, fixture="safe", confirmation="main\n", **options):
        """撤去のモードで実行する。撤去の手順（ガイド 5.4）と同じく、MFA 付きのロールの profile を使う。"""
        return self.run_deploy(
            fixture,
            confirmation,
            profile="registry-deploy",
            profiles=ROLE_PROFILES,
            destroy=True,
            **options,
        )

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
                        if item in {
                            "plan", "apply", "sync", "cp", "create-invalidation",
                            "create-deployment", "update-stage",
                        }
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
                ("aws", "create-deployment"),
                ("aws", "update-stage"),
                ("aws", "sync"),
                ("aws", "cp"),
                ("aws", "create-invalidation"),
            ],
        )
        plan = next(command for command in commands if "plan" in command)
        guard = next(
            command
            for command in commands
            if command[0] == "python3" and command[1].endswith("check_plan.py")
        )
        apply = next(command for command in commands if "apply" in command)
        self.assertEqual(next(item[5:] for item in plan if item.startswith("-out=")), apply[-1])
        self.assertEqual(guard[2:], ["--saved-plan", apply[-1]])
        self.assertLess(commands.index(plan), commands.index(guard))
        self.assertLess(commands.index(guard), commands.index(apply))
        init = next(command for command in commands if "init" in command)
        self.assertIn("-backend-config=encrypt=true", init)
        self.assertIn("-backend-config=use_lockfile=true", init)
        # 本体スタックは output rest_api の ID とステージ名で再デプロイする（ステージ名を直書きしない）
        deploy = next(command for command in commands if "create-deployment" in command)
        update = next(command for command in commands if "update-stage" in command)
        self.assertEqual(deploy[deploy.index("--rest-api-id") + 1], "abcde12345")
        self.assertEqual(update[update.index("--stage-name") + 1], "stage-from-output")

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
        for stack_args in [(), ("--stack", "decision")]:
            with self.subTest(stack_args=stack_args):
                result, commands = self.run_deploy(ci="true", stack_args=stack_args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CI", result.stderr)
                self.assertEqual(commands, [])

    def test_addon_stack_runs_the_same_guarded_flow_in_its_directory_without_spa_upload(self):
        # 追加スタックも本体と同じ主体の検査・plan ガード・確認を通し、SPA の配信は行わない
        for stack, needs_zip in [
            ("decision", True),
            ("notification_events", True),
            ("management", False),
        ]:
            with self.subTest(stack=stack):
                result, commands = self.run_deploy(
                    profile="registry-deploy", profiles=ROLE_PROFILES, stack_args=("--stack", stack)
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                terraform = [record for record in self.records if record["argv"][0] == "terraform"]
                stack_dir = ROOT / "infrastructure" / stack
                self.assertTrue(terraform)
                self.assertTrue(
                    all(
                        record["argv"][1] == f"-chdir={stack_dir}"
                        or Path(record["cwd"]).resolve() == stack_dir
                        for record in terraform
                    )
                )
                terraform = [record["argv"] for record in terraform]
                init = next(command for command in terraform if "init" in command)
                self.assertIn("-backend-config=use_lockfile=true", init)
                self.assertTrue(
                    any(
                        command[0] == "python3" and command[1].endswith("check_plan.py")
                        for command in commands
                    )
                )
                self.assertTrue(any("apply" in command for command in terraform))
                api_commands = [command for command in commands if command[:2] == ["aws", "apigateway"]]
                self.assertEqual(
                    [command[2] for command in api_commands],
                    [] if stack == "notification_events" else ["create-deployment", "update-stage"],
                )
                if api_commands:
                    self.assertLess(
                        commands.index(next(command for command in terraform if "apply" in command)),
                        commands.index(api_commands[0]),
                    )
                plan = next(command for command in terraform if "plan" in command)
                builds = [
                    command
                    for command in commands
                    if command[0] == "python3" and command[1].endswith("build_lambda.py")
                ]
                if needs_zip:
                    zip_path = builds[0][builds[0].index("--output") + 1]
                    self.assertIn(f"-var=lambda_zip_path={zip_path}", plan)
                else:
                    self.assertEqual(builds, [])
                    self.assertFalse(any(item.startswith("-var=lambda_zip_path") for item in plan))
                self.assertFalse(any(command[0] == "npm" for command in commands))
                self.assertFalse(
                    any("s3" in command or "cloudfront" in command for command in commands)
                )

    def test_addon_stack_rejects_unsafe_principal_and_guard_failure(self):
        other_session = "arn:aws:sts::123456789012:assumed-role/Administrator/operator"
        profiles = with_profile(
            with_profile(ROLE_PROFILES, "registry-deploy", caller_arn=other_session),
            "registry-deploy-tf",
            caller_arn=other_session,
        )
        result, commands = self.run_deploy(
            profile="registry-deploy", profiles=profiles, stack_args=("--stack", "decision")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(command[0] == "terraform" for command in commands))
        result, commands = self.run_deploy(
            fixture="registry-update",
            profile="registry-deploy",
            profiles=ROLE_PROFILES,
            stack_args=("--stack", "decision"),
        )
        self.assertIn("check_plan.py", " ".join(" ".join(command) for command in commands))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("apply" in command for command in commands))

    def test_sso_profile_is_rejected_for_addon_stacks_before_terraform(self):
        # Identity Center の permission set は本体向けの権限だけを持つため、追加スタックは個人アカウントの
        # MFA 付きロールの profile でだけ反映する
        for stack in ("management", "decision", "notification_events"):
            with self.subTest(stack=stack):
                result, commands = self.run_deploy(stack_args=("--stack", stack))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Identity Center", result.stderr)
                self.assertFalse(any(command[0] == "terraform" for command in commands))
                self.assertFalse(
                    any(command[1:3] == ["sts", "get-caller-identity"] for command in commands)
                )

    def test_backend_state_key_of_another_stack_is_rejected_before_external_commands(self):
        # 本体の backend.hcl を追加スタックに渡す（またはその逆）と、plan に他のスタックの資源の削除が出る
        for stack_args, backend_key in [
            (("--stack", "decision"), STATE_KEYS["main"]),
            (("--stack", "management"), STATE_KEYS["decision"]),
            ((), STATE_KEYS["notification_events"]),
            (("--stack", "notification_events"), ""),
        ]:
            with self.subTest(stack_args=stack_args, backend_key=backend_key):
                result, commands = self.run_deploy(
                    profile="registry-deploy",
                    profiles=ROLE_PROFILES,
                    stack_args=stack_args,
                    backend_key=backend_key,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("state key", result.stderr)
                self.assertEqual(commands, [])

    def test_backend_examples_use_the_state_keys_the_script_expects(self):
        infrastructure = ROOT / "infrastructure"
        for stack, key in STATE_KEYS.items():
            directory = infrastructure if stack == "main" else infrastructure / stack
            with self.subTest(stack=stack):
                example = (directory / "backend.hcl.example").read_text()
                self.assertIn(f'key          = "{key}"', example)
                self.assertIn(f'state_key="{key}"', (ROOT / "scripts/deploy.sh").read_text())

    def test_addon_backend_examples_share_the_main_state_bucket(self):
        # 本体の撤去では、本体の backend.hcl の key だけを差し替えて追加スタックの state を読む
        infrastructure = ROOT / "infrastructure"

        def settings(path):
            lines = path.read_text().splitlines()
            return dict(
                (part.strip().strip('"') for part in line.split("=", 1))
                for line in lines
                if "=" in line and not line.lstrip().startswith("#")
            )

        main = settings(infrastructure / "backend.hcl.example")
        for stack in ADDON_STACKS:
            with self.subTest(stack=stack):
                addon = settings(infrastructure / stack / "backend.hcl.example")
                self.assertEqual({**addon, "key": main["key"]}, main)

    def test_unknown_stack_is_rejected_before_external_commands(self):
        for stack_args in [
            ("--stack", "bootstrap"),
            ("--stack", "../decision"),
            ("--stack", "main"),
            ("--stack",),
        ]:
            with self.subTest(stack_args=stack_args):
                result, commands = self.run_deploy(stack_args=stack_args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(commands, [])

    def test_repeated_options_are_rejected_before_external_commands(self):
        # 後の指定を黙って使うと、撤去や反映の対象を取り違えるおそれがあるため止める
        for stack_args, option in [
            (("--destroy", "--destroy"), "--destroy"),
            (("--stack", "decision", "--stack", "management"), "--stack"),
            (("--stack", "decision", "--destroy", "--stack", "decision"), "--stack"),
        ]:
            with self.subTest(stack_args=stack_args):
                result, commands = self.run_deploy(stack_args=stack_args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{option} は 1 回だけ指定してください", result.stderr)
                self.assertEqual(commands, [])

    def test_options_can_be_given_in_any_order(self):
        result, commands = self.run_deploy(
            confirmation="decision\n",
            profile="registry-deploy",
            profiles=ROLE_PROFILES,
            stack_args=("--stack", "decision", "--destroy"),
            backend_key=STATE_KEYS["decision"],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plan = next(command for command in commands if "plan" in command)
        self.assertEqual(plan[1], f"-chdir={ROOT / 'infrastructure' / 'decision'}")
        self.assertIn("-destroy", plan)

    def test_destroy_applies_the_saved_destroy_plan_without_spa_upload(self):
        # destroy のモードは plan -destroy を保存し、確認後にその plan を apply する。SPA は配信しない
        result, commands = self.run_deploy(destroy=True, confirmation="main\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        terraform = [command for command in commands if command[0] == "terraform"]
        plan = next(command for command in terraform if "plan" in command)
        apply = next(command for command in terraform if "apply" in command)
        self.assertIn("-destroy", plan)
        self.assertEqual(next(item[5:] for item in plan if item.startswith("-out=")), apply[-1])
        self.assertLess(terraform.index(plan), terraform.index(apply))
        self.assertTrue(
            any(
                command[0] == "python3" and command[1].endswith("check_plan.py")
                for command in commands
            )
        )
        self.assertFalse(any(command[0] == "npm" for command in commands))
        self.assertFalse(any("s3" in command or "cloudfront" in command for command in commands))

    def test_destroy_guard_failure_or_unmatched_confirmation_never_reaches_apply(self):
        # 撤去の確認は配信より強く、スタック名と完全に一致しない入力（y や apply を含む）では止まる
        cases = [
            ("registry-update", "main\n", (), "check_plan"),
            ("safe", "y\n", (), "一致しない"),
            ("safe", "apply\n", (), "一致しない"),
            ("safe", "main \n", (), "一致しない"),
            ("safe", "", (), "中止"),
            ("safe", "main\n", ("--stack", "decision"), "一致しない"),
        ]
        for fixture, confirmation, stack_args, reason in cases:
            with self.subTest(fixture=fixture, confirmation=confirmation, stack_args=stack_args):
                result, commands = self.run_destroy(
                    fixture,
                    confirmation,
                    stack_args=stack_args,
                )
                self.assertNotEqual(result.returncode, 0)
                if reason != "check_plan":
                    self.assertIn(reason, result.stderr)
                self.assertTrue(any("plan" in command for command in commands))
                self.assertFalse(any("apply" in command for command in commands))

    def test_main_destroy_stops_while_an_addon_stack_state_has_resources(self):
        # 追加スタックは本体の API・Lambda・ロール・テーブルを借りるため、撤去の順番は「追加スタック → 本体」
        result, commands = self.run_destroy(states={"management": pulled_state(LOG_GROUP)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("management（1 件）", result.stderr)
        self.assertNotIn("decision", result.stderr)
        terraform = [command for command in commands if command[0] == "terraform"]
        self.assertIn(
            [
                "terraform",
                f"-chdir={ROOT / 'infrastructure' / 'management'}",
                "state",
                "pull",
            ],
            terraform,
        )
        self.assertFalse(any("plan" in command or "apply" in command for command in terraform))
        self.assertFalse(
            any(command[1] == f"-chdir={ROOT / 'infrastructure'}" for command in terraform)
        )

    def test_main_destroy_stops_when_an_addon_backend_points_to_another_bucket(self):
        # 追加スタックの state は、本体の backend.hcl の key だけを差し替えて読む。手元の追加スタックの
        # backend.hcl が別のバケットを指していれば、その前提が崩れるため、state を読む前に止める
        result, commands = self.run_destroy(
            addon_backends={"management": 'bucket = "another-state-bucket"\n'}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("management", result.stderr)
        self.assertIn("本体の backend.hcl と一致しません", result.stderr)
        self.assertFalse(any(command[0] == "terraform" for command in commands))

    def test_main_destroy_reports_how_each_addon_backend_is_resolved(self):
        same_bucket = (
            'bucket = "registry-portal-state-123456789012"\n'
            f'key    = "{STATE_KEYS["decision"]}"\n'
        )
        result, commands = self.run_destroy(addon_backends={"decision": same_bucket})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "decision: backend.hcl のバケットとリージョンが本体と一致しました", result.stdout
        )
        self.assertIn("management: ", result.stdout)
        self.assertIn("がないため、本体の backend.hcl のバケットとリージョンで", result.stdout)

    def test_main_destroy_stops_when_an_addon_state_cannot_be_read(self):
        # 認証やネットワークの失敗、JSON の破損は「state なし」や 0 件として扱わない
        cases = {
            "state pull の失敗": ({}, {"FAKE_STATE_PULL_FAILS": "management"}),
            "JSON の破損": ({"management": {"version": 3}}, {}),
        }
        for label, (states, extra_env) in cases.items():
            with self.subTest(label):
                result, commands = self.run_destroy(
                    states=states,
                    extra_env=extra_env,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("plan" in command for command in commands))

    def test_main_destroy_proceeds_when_addon_states_are_empty(self):
        # 撤去済みの追加スタックの state は、リソースのない state か、state のファイル自体がない
        result, commands = self.run_destroy(states={"decision": pulled_state()})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 撤去済み（0 件）と未配信（state なし）は、どちらも撤去を止めないが表示では区別する
        self.assertIn("decision: 0 件", result.stdout)
        self.assertIn("management: state なし（未配信）", result.stdout)
        for addon in ADDON_STACKS:
            with self.subTest(addon=addon):
                init = next(
                    command
                    for command in commands
                    if command[:2] == ["terraform", f"-chdir={ROOT / 'infrastructure' / addon}"]
                    and "init" in command
                )
                self.assertIn(f"-backend-config=key={STATE_KEYS[addon]}", init)
        self.assertTrue(any("apply" in command for command in commands))

    def test_addon_state_init_reuses_the_main_backend_settings_with_only_the_key_replaced(self):
        # 追加スタックの state は本体と同じバケットにあり、key だけが異なる（各 backend.hcl.example）
        result, commands = self.run_destroy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        inits = {
            command[1]: (command[2:], record["profile"])
            for command, record in zip(commands, self.records)
            if command[0] == "terraform" and "init" in command
        }
        main_args, main_profile = inits[f"-chdir={ROOT / 'infrastructure'}"]
        for addon in ADDON_STACKS:
            with self.subTest(addon=addon):
                args, profile = inits[f"-chdir={ROOT / 'infrastructure' / addon}"]
                key = f"-backend-config=key={STATE_KEYS[addon]}"
                self.assertEqual([item for item in args if item != key], main_args)
                backend = next(item for item in args if item.endswith("backend.hcl"))
                # 後から指定した値が優先されるため、key は backend.hcl の後に置く
                self.assertLess(args.index(backend), args.index(key))
                self.assertEqual(profile, main_profile)
        checks = [
            command
            for command in commands
            if command[0] == "python3" and command[1].endswith("check_teardown.py")
        ]
        self.assertEqual(
            [command[2] for command in checks],
            ["addon-backends", "addon-states", "main-protection"],
        )

    def test_main_destroy_stops_before_apply_while_protection_remains_in_state(self):
        # 手順 3 の apply を飛ばすと、保護のないリソースだけ消えてから途中で失敗するため、apply の前に止める
        result, commands = self.run_destroy("destroy-main-protected")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("5.4", result.stderr)
        self.assertNotIn("スタック名（main）を入力", result.stdout)
        self.assertFalse(any("apply" in command for command in commands))

    def test_main_destroy_applies_after_protection_is_removed_from_state(self):
        result, commands = self.run_destroy("destroy-main")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("apply" in command for command in commands))

    def test_addon_destroy_does_not_require_the_main_protection_settings(self):
        result, commands = self.run_destroy(
            "destroy-main-protected",
            "decision\n",
            stack_args=("--stack", "decision"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        apply = next(command for command in commands if "apply" in command)
        deploy = next(command for command in commands if "create-deployment" in command)
        update = next(command for command in commands if "update-stage" in command)
        self.assertLess(commands.index(apply), commands.index(deploy))
        self.assertLess(commands.index(deploy), commands.index(update))
        self.assertIn("op=replace,path=/deploymentId,value=deployment123", update)
        # 撤去で state が消えても、plan の変数 rest_api からステージを決める
        self.assertEqual(update[update.index("--stage-name") + 1], "stage-from-plan")
        self.assertFalse(
            any(command[0] == "python3" and "check_teardown" in command[1] for command in commands)
        )

    def test_usage_shows_destroy_mode_and_destroy_is_rejected_from_ci(self):
        result = subprocess.run(
            [str(ROOT / "scripts/deploy.sh")],
            text=True,
            capture_output=True,
            env={**os.environ, "CI": ""},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[--destroy]", result.stderr)
        self.assertIn("各オプションは 1 回だけ", result.stderr)
        for stack_args in [(), ("--stack", "decision")]:
            with self.subTest(stack_args=stack_args):
                result, commands = self.run_deploy(ci="true", stack_args=stack_args, destroy=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CI", result.stderr)
                self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
