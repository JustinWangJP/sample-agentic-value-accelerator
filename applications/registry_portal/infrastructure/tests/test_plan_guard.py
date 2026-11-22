import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
# 追加スタックのポリシーテンプレートに埋める値（plan で確定している想定）
TEMPLATE_VALUES = {
    "registry_arn": "arn:aws:agent-registry:us-east-1:123456789012:registry/portal",
    "supplement_table_arn": (
        "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-supplement"
    ),
    "discovery_secret_arn": (
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
    ),
    "log_group_arn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/portal",
    "dead_letter_queue_arn": "arn:aws:sqs:us-east-1:123456789012:registry-portal-events-dlq",
    "kms_key_arn": "arn:aws:kms:us-east-1:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab",
    "secret_region": "us-east-1",
}


def run_guard(plan_path, *args, **kwargs):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_plan.py"), str(plan_path), *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


def run_guard_json(plan):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "plan.json"
        path.write_text(json.dumps(plan))
        return run_guard(path)


def run_guard_saved(
    plan, sources=None, manifest=None, show_status=0, raw_snapshot=None, duplicate_entry=False
):
    if sources is None:
        sources = {"": {"main.tf": b"terraform {}\n"}, "events": {"main.tf": b"terraform {}\n"}}
    if manifest is None:
        manifest = [{"Key": key, "Dir": "."} for key in sources]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        saved = root / "saved.tfplan"
        if raw_snapshot is None:
            with zipfile.ZipFile(saved, "w") as archive:
                archive.writestr("tfconfig/modules.json", json.dumps(manifest))
                if duplicate_entry:
                    archive.writestr("tfconfig/modules.json", json.dumps(manifest))
                for key, files in sources.items():
                    for name, source in files.items():
                        archive.writestr(f"tfconfig/m-{key}/{name}", source)
        else:
            saved.write_bytes(raw_snapshot)
        (root / "show.json").write_text(json.dumps(plan))
        terraform = root / "terraform"
        terraform.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, sys\n"
            "root = pathlib.Path(__file__).parent\n"
            "assert pathlib.Path.cwd() == root\n"
            "assert sys.argv[1:] == ['show', '-json', str(root / 'saved.tfplan')]\n"
            "print((root / 'show.json').read_text())\n"
            "print('表示しない検証用メッセージ', file=sys.stderr)\n"
            f"sys.exit({show_status})\n"
        )
        terraform.chmod(0o755)
        env = dict(os.environ, PATH=f"{root}{os.pathsep}{os.environ.get('PATH', '')}")
        return run_guard("--saved-plan", "saved.tfplan", cwd=root, env=env)


class PlanGuardTests(unittest.TestCase):
    def test_registry_update_is_rejected(self):
        result = run_guard(FIXTURES / "registry-update.json")
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
                result = run_guard(FIXTURES / f"{fixture}.json")
                self.assertEqual(
                    result.returncode, 0 if allowed else 1, result.stdout + result.stderr
                )

    def test_destroy_plan_allows_portal_deletions_and_rejects_registry_deletions(self):
        # terraform plan -destroy の JSON は、planned_values の root_module が空で、
        # 各 resource_changes の after が null になる（Terraform 1.17.1 で確認した形）
        base = json.loads((FIXTURES / "destroy-main.json").read_text())
        self.assertEqual(base["planned_values"], {"root_module": {}})

        def deletion(address, resource_type, before):
            return {
                "address": address,
                "mode": "managed",
                "type": resource_type,
                "name": address.split(".")[-1],
                "change": {"actions": ["delete"], "before": before, "after": None},
            }

        registry_arn = TEMPLATE_VALUES["registry_arn"]
        # 削除される policy に Registry の書き込みがあっても、権限の付与ではないため止めない
        write_policy = json.dumps(
            {
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "agent-registry:CreateRegistryRecord",
                        "Resource": "*",
                    }
                ]
            }
        )
        cases = {
            "ポータルのリソースだけの削除": ([], True),
            "Registry の書き込みを含む policy の削除": (
                [
                    deletion(
                        "module.management_drafts.aws_iam_role_policy.management_drafts",
                        "aws_iam_role_policy",
                        {"name": "management-drafts", "policy": write_policy},
                    )
                ],
                True,
            ),
            "Registry の種類のリソースの削除": (
                [
                    deletion(
                        "aws_agent_registry_registry.existing", "aws_agent_registry_registry", {}
                    )
                ],
                False,
            ),
            "Registry の ARN を持つリソースの削除": (
                [
                    deletion(
                        "aws_cloudcontrolapi_resource.registry",
                        "aws_cloudcontrolapi_resource",
                        {"id": registry_arn},
                    )
                ],
                False,
            ),
        }
        for label, (extra, allowed) in cases.items():
            with self.subTest(label):
                plan = dict(base, resource_changes=[*base["resource_changes"], *extra])
                result = run_guard_json(plan)
                self.assertEqual(
                    result.returncode, 0 if allowed else 1, result.stdout + result.stderr
                )
                if not allowed:
                    self.assertIn("既存 Registry", result.stderr)

    def test_registry_write_actions_are_allowed_only_for_their_own_role(self):
        # 管理ロール（management_drafts）は下書きの作成・更新・申請、
        # 決裁ロール（decision）は状態遷移だけを、それぞれの inline policy に限って許可する
        for fixture, allowed in [
            ("management-drafts", True),
            ("decision-status", True),
            # 決裁ロールに descriptor の作成・更新・申請は与えない
            ("decision-drafts", False),
            ("decision-submit", False),
            # 管理ロールにも状態遷移・削除・ワイルドカード・旧 prefix は与えない
            ("management-status", False),
            ("management-delete", False),
            ("management-wildcard", False),
            ("management-create-wildcard", False),
            ("management-legacy-prefix", False),
            # イベント処理ロールには引き続き禁止する
            ("events-status", False),
            ("events-drafts", False),
            # 同じ名前でも、任意の主体に付けられる aws_iam_policy は対象外
            ("management-drafts-managed-policy", False),
        ]:
            with self.subTest(fixture=fixture):
                result = run_guard(FIXTURES / f"{fixture}.json")
                self.assertEqual(
                    result.returncode, 0 if allowed else 1, result.stdout + result.stderr
                )
                if not allowed:
                    self.assertIn("禁止 action", result.stderr)

    def test_role_scoped_registry_writes_are_limited_to_the_target_registry(self):
        # 自ロールの書き込み action でも、Resource が plan の変数 registry_arn の Registry と
        # そのレコードに限られない場合は中止する
        for fixture in [
            "management-drafts-wildcard-resource",
            "management-drafts-other-registry",
            # 前方一致のワイルドカードは別 Registry（registry/portal-other など）にも一致する
            "management-drafts-registry-prefix",
            # 対象 Registry が plan からわからなければ検査できない
            "management-drafts-no-registry-variable",
            "decision-status-wildcard-resource",
            "decision-status-other-registry",
        ]:
            with self.subTest(fixture=fixture):
                result = run_guard(FIXTURES / f"{fixture}.json")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("対象 Registry", result.stderr)

    def test_additional_stack_policy_templates_pass_the_guard(self):
        # 追加スタックの実際のポリシー本文を、各スタックの plan と同じ資源アドレスで検査する。
        # テンプレートに他ロールの action を足すと、ここで中止される
        modules = ROOT / "infrastructure/modules"
        for address, template in [
            (
                "module.management_drafts.aws_iam_role_policy.management_drafts",
                modules / "management_drafts/draft-policy.json.tftpl",
            ),
            (
                "module.decision.aws_iam_role_policy.decision",
                modules / "decision/decision-policy.json.tftpl",
            ),
            (
                # 発見用 secret が顧客管理 KMS キーで暗号化されている場合だけ付ける policy（#49）
                "module.decision.aws_iam_role_policy.decision_discovery_kms[0]",
                modules / "discovery_client/discovery-kms-policy.json.tftpl",
            ),
            (
                "module.notification_events.aws_iam_role_policy.events",
                modules / "notification_events/events-policy.json.tftpl",
            ),
        ]:
            with self.subTest(address=address):
                policy = re.sub(
                    r"\$\{(\w+)\}",
                    lambda match: TEMPLATE_VALUES[match.group(1)],
                    template.read_text(),
                )
                resource = {
                    "address": address,
                    "mode": "managed",
                    "type": "aws_iam_role_policy",
                    "values": {"role": "registry-portal", "policy": policy},
                }
                plan = {
                    "format_version": "1.2",
                    # 追加スタックのルート変数。ガードは書き込み action の Resource をこの値と照合する
                    "variables": {"registry_arn": {"value": TEMPLATE_VALUES["registry_arn"]}},
                    "resource_changes": [],
                    "planned_values": {"root_module": {"resources": [resource]}},
                }
                result = run_guard_json(plan)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_function_url_is_rejected_from_either_plan_section(self):
        # Function URL は planned_values と resource_changes のどちらに現れても中止する
        resource = {
            "address": "aws_lambda_function_url.api",
            "mode": "managed",
            "type": "aws_lambda_function_url",
            "values": {"function_name": "registry-portal", "authorization_type": "NONE"},
        }
        for source, after in [
            ("planned_values", None),
            ("resource_changes", resource["values"]),
            # 削除だけの変更（after が null）でも検出する
            ("resource_changes", None),
        ]:
            with self.subTest(source=source, after=after):
                plan = {
                    "format_version": "1.2",
                    "resource_changes": [],
                    "planned_values": {"root_module": {"resources": []}},
                }
                if source == "planned_values":
                    plan["planned_values"]["root_module"]["resources"] = [resource]
                else:
                    change = {key: resource[key] for key in ("address", "mode", "type")}
                    plan["resource_changes"] = [dict(change, change={"after": after})]
                result = run_guard_json(plan)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("Function URL は使用できません", result.stderr)

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
                    result = run_guard_json(plan)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("managed policy", result.stderr)

    def test_role_creation_with_provider_computed_policies_requires_saved_sources(self):
        # AWS provider 6.71.0 の実際の plan JSON。ロールの作成時は、設定で policy を付けていなくても
        # managed_policy_arns と inline_policy が未確定になる（managed_policy_arns = [] も未指定として扱われる）
        plan = json.loads((FIXTURES / "create-role-computed-policies.json").read_text())
        roles = {
            change["address"]: change["change"]
            for change in plan["resource_changes"]
            if change["type"] == "aws_iam_role"
        }
        self.assertEqual(
            set(roles), {"aws_iam_role.management", "module.events.aws_iam_role.events[0]"}
        )
        for change in roles.values():
            self.assertEqual(change["actions"], ["create"])
            self.assertTrue(change["after_unknown"]["managed_policy_arns"])
            self.assertTrue(change["after_unknown"]["inline_policy"])

        result = run_guard(FIXTURES / "create-role-computed-policies.json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未確定の IAM policy は検査できません", result.stderr)

        result = run_guard_saved(plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_saved_source_verification_rejects_ambiguous_or_unavailable_sources(self):
        plan = json.loads((FIXTURES / "create-role-computed-policies.json").read_text())
        events = {"events": {"main.tf": b"terraform {}\n"}}
        cases = {
            "コメントに dynamic がある": {"sources": {"": {"main.tf": b"# dynamic\n"}, **events}},
            "JSON 設定がある": {
                "sources": {"": {"main.tf": b"terraform {}\n", "extra.tf.json": b"{}"}, **events}
            },
            "native HCL がない": {"sources": {"": {"notes.txt": b""}, **events}},
            "root のソースがない": {"sources": {"events": {"main.tf": b"terraform {}\n"}}},
            "module のソースがない": {"sources": {"": {"main.tf": b"terraform {}\n"}}},
            "manifest が未知の形式": {"manifest": {"Modules": [{"Key": ""}]}},
            "manifest の Key が重複": {"manifest": [{"Key": ""}, {"Key": ""}]},
            "ZIP が読めない": {"raw_snapshot": b""},
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                result = run_guard_saved(plan, **kwargs)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("未確定の IAM policy は検査できません", result.stderr)
                if label in {"コメントに dynamic がある", "JSON 設定がある", "native HCL がない"}:
                    self.assertIn("aws_iam_role.management:", result.stderr)
                    self.assertNotIn("module.events.aws_iam_role.events[0]:", result.stderr)
        with self.assertWarns(UserWarning):
            result = run_guard_saved(plan, duplicate_entry=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未確定の IAM policy は検査できません", result.stderr)

    def test_saved_sources_match_indexed_and_nested_module_instances(self):
        base = json.loads((FIXTURES / "create-role-computed-policies.json").read_text())
        for address in [
            "module.events[0]",
            'module.events["east"]',
            'module.events["a]b.c"]',
            'module.events["a\\"b\\\\c]"].module.worker[2]',
        ]:
            with self.subTest(address):
                plan = json.loads(json.dumps(base))
                role = plan["resource_changes"][1]
                role["module_address"] = address
                role["address"] = f"{address}.aws_iam_role.events[0]"
                events = plan["configuration"]["root_module"]["module_calls"]["events"]
                key = "events"
                if ".module.worker" in address:
                    events["module"] = {"module_calls": {"worker": events.copy()}}
                    key = "events.worker"
                sources = {
                    "": {"main.tf": b"terraform {}\n"},
                    key: {"main.tf": b"terraform {}\n"},
                }
                result = run_guard_saved(plan, sources=sources)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # HCL 識別子に使える結合文字も configuration と snapshot の同じ名前で照合する。
        plan = json.loads(json.dumps(base))
        name = "eve\u0301nts"
        calls = plan["configuration"]["root_module"]["module_calls"]
        calls[name] = calls.pop("events")
        plan["resource_changes"][1]["module_address"] = f"module.{name}"
        plan["resource_changes"][1]["address"] = f"module.{name}.aws_iam_role.events[0]"
        result = run_guard_saved(
            plan,
            sources={"": {"main.tf": b"terraform {}\n"}, name: {"main.tf": b"terraform {}\n"}},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_saved_source_confirmation_does_not_exempt_other_unknown_policy_shapes(self):
        base = json.loads((FIXTURES / "create-role-computed-policies.json").read_text())
        for key, value in [
            ("inline_policy", [{"policy": True}]),
            ("policy", True),
        ]:
            with self.subTest(key=key):
                plan = json.loads(json.dumps(base))
                plan["resource_changes"][0]["change"]["after_unknown"][key] = value
                result = run_guard_saved(plan)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("未確定の IAM policy は検査できません", result.stderr)

    def test_terraform_show_failure_hides_its_output_and_rejects_the_plan(self):
        plan = json.loads((FIXTURES / "safe.json").read_text())
        result = run_guard_saved(plan, show_status=1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("保存 plan を Terraform で読み取れません", result.stderr)
        self.assertNotIn("表示しない検証用メッセージ", result.stderr + result.stdout)
        self.assertNotIn("format_version", result.stderr + result.stdout)

    def test_guard_usage_errors_are_in_japanese(self):
        for args in [[], ["--saved-plan"], ["--other"], ["a", "b"], ["--saved-plan", "a", "b"]]:
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/check_plan.py"), *args],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("使い方", result.stderr)

    def test_role_creation_with_configured_unknown_policies_is_rejected(self):
        # 設定で未確定の値を渡す場合は、作成時でも中止する（AWS provider 6.71.0 の実際の plan JSON）
        for fixture, message in [
            (
                "create-role-unknown-managed-policy",
                "未確定の managed policy の付与は検査できません",
            ),
            ("create-role-unknown-inline-policy", "未確定の IAM policy は検査できません"),
        ]:
            with self.subTest(fixture=fixture):
                plan = json.loads((FIXTURES / f"{fixture}.json").read_text())
                result = run_guard_saved(plan)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"aws_iam_role.management: {message}", result.stderr)

    def test_unknown_role_policies_are_rejected_without_creation_or_configuration(self):
        # 作成以外の変更や、設定を照合できない plan では、未確定の policy をこれまでどおり中止する
        base = json.loads((FIXTURES / "create-role-computed-policies.json").read_text())

        def update_only(plan):
            for change in plan["resource_changes"]:
                change["change"]["actions"] = ["update"]

        def unknown_module(plan):
            plan["configuration"]["root_module"]["module_calls"].clear()

        def missing_expressions(plan):
            plan["configuration"]["root_module"]["resources"][0].pop("expressions")

        cases = {
            "作成以外の変更": update_only,
            "configuration がない": lambda plan: plan.pop("configuration"),
            "module の設定がない": unknown_module,
            "resource の式が確認できない": missing_expressions,
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                plan = json.loads(json.dumps(base))
                mutate(plan)
                result = run_guard_saved(plan)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("未確定の IAM policy は検査できません", result.stderr)

    def test_role_scoped_policies_exist_in_the_addon_stack_definitions(self):
        # 書き込みを許可する policy は、別 state の追加スタックの module 名と resource 名で特定する。
        # 定義の改名に追従しないと許可が外れるため、定義に実在することを確かめる
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import check_plan
        finally:
            sys.path.pop(0)
        stack_sources = "\n".join(
            path.read_text() for path in (ROOT / "infrastructure").glob("*/main.tf")
        )
        self.assertTrue(check_plan.ROLE_SCOPED_ACTIONS)
        for policy in check_plan.ROLE_SCOPED_ACTIONS:
            with self.subTest(address=policy.address):
                module_block = re.search(
                    rf'module "{policy.module}" \{{[^}}]*source\s*=\s*"\.\./modules/([a-z_]+)"',
                    stack_sources,
                )
                self.assertIsNotNone(module_block)
                module_main = (
                    ROOT / "infrastructure/modules" / module_block.group(1) / "main.tf"
                ).read_text()
                self.assertIn(f'resource "aws_iam_role_policy" "{policy.name}"', module_main)

    def test_bootstrap_denies_the_same_registry_write_actions_as_the_guard(self):
        # 書き込み action の一覧は 1 つのファイルに置き、ガードと bootstrap の Deny の両方が読む
        bootstrap = (ROOT / "infrastructure/bootstrap/main.tf").read_text()
        self.assertIn(
            'jsondecode(file("${path.module}/../../scripts/registry_write_actions.json"))',
            bootstrap,
        )
        self.assertIn("Action   = local.registry_write_actions", bootstrap)

    def test_forbidden_actions_cover_both_registry_service_prefixes(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import check_plan
        finally:
            sys.path.pop(0)
        for action in check_plan.REGISTRY_WRITE_ACTIONS:
            for prefix in ("agent-registry", "bedrock-agentcore"):
                with self.subTest(action=f"{prefix}:{action}"):
                    self.assertIn(f"{prefix}:{action}", check_plan.FORBIDDEN_ACTIONS)


if __name__ == "__main__":
    unittest.main()
