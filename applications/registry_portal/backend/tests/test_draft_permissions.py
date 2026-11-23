import json
import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "infrastructure/modules/management_drafts"
REGISTRY_ARN = "arn:aws:agent-registry:ap-northeast-1:123456789012:registry/abcdefghijkl"
TABLE_ARN = "arn:aws:dynamodb:ap-northeast-1:123456789012:table/registry-portal-supplement"


def render_policy() -> dict:
    """templatefile と同じく、テンプレートの変数を置き換えて JSON として読む。"""
    template = (MODULE / "draft-policy.json.tftpl").read_text()
    rendered = template.replace("${registry_arn}", REGISTRY_ARN).replace(
        "${supplement_table_arn}", TABLE_ARN
    )
    assert "${" not in rendered
    return json.loads(rendered)


def test_draft_policy_only_grants_record_writes_and_supplement_transactions():
    assert render_policy()["Statement"] == [
        {
            "Sid": "RegistryDrafts",
            "Effect": "Allow",
            "Action": [
                "agent-registry:CreateRegistryRecord",
                "agent-registry:UpdateRegistryRecord",
                "agent-registry:GetRegistryRecord",
                "agent-registry:ListRegistryRecords",
                "agent-registry:GetRegistry",
                "agent-registry:SubmitRegistryRecordForApproval",
            ],
            "Resource": [REGISTRY_ARN, f"{REGISTRY_ARN}/record/*"],
        },
        {
            "Sid": "DraftSupplement",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                # 廃止依頼の保存が、ロックがないことをトランザクションの中で確かめるために使う
                "dynamodb:ConditionCheckItem",
            ],
            "Resource": TABLE_ARN,
        },
    ]


def test_module_only_attaches_the_policy_to_the_existing_management_role():
    main = (MODULE / "main.tf").read_text()
    assert re.findall(r'^resource "([^"]+)"', main, re.MULTILINE) == ["aws_iam_role_policy"]
    assert "role = var.management_role_name" in main
    assert 'templatefile("${path.module}/draft-policy.json.tftpl"' in main


def test_arn_validation_does_not_pin_a_region():
    variables = (MODULE / "variables.tf").read_text()
    assert "us-east-1" not in variables
    for arn in (REGISTRY_ARN, TABLE_ARN):
        assert any(
            re.fullmatch(pattern, arn) for pattern in re.findall(r'regex\("([^"]+)"', variables)
        )


ROOT = MODULE.parents[1] / "management"


def test_root_module_applies_the_draft_policy_to_the_management_role():
    """下書きモジュールが、ルートモジュールから管理 Lambda のロールへ適用される経路を持つ。"""
    main = (ROOT / "main.tf").read_text()
    (block,) = re.findall(r'module "management_drafts" \{(.*?)\n\}', main, re.DOTALL)
    source = re.search(r'source\s*=\s*"([^"]+)"', block)
    assert source is not None
    assert (ROOT / source.group(1)).resolve() == MODULE
    module_variables = set(
        re.findall(r'^variable "([^"]+)"', (MODULE / "variables.tf").read_text(), re.MULTILINE)
    )
    for name in module_variables:
        assert re.search(rf"^\s*{name}\s*=\s*var\.{name}$", block, re.MULTILINE)
    root_variables = set(
        re.findall(r'^variable "([^"]+)"', (ROOT / "variables.tf").read_text(), re.MULTILINE)
    )
    # REST API の ID・authorizer・scope は本体の output rest_api の転記から、統合先は関数名から取得する
    routes_inputs = {"rest_api", "management_function_name"}
    assert root_variables == module_variables | routes_inputs | {"region"}
    # CI では検証だけを行うため、state の保存先は部分設定にして環境ごとに渡す
    assert 'backend "s3" {}' in (ROOT / "versions.tf").read_text()


ROUTES_MODULE = MODULE.parent / "api_routes"


def _variables(module: Path) -> set[str]:
    return set(
        re.findall(r'^variable "([^"]+)"', (module / "variables.tf").read_text(), re.MULTILINE)
    )


# 管理スタックの経路の中身は、管理 Lambda のルート表と照合する（tests/test_route_tables.py）


def test_root_module_applies_the_routes_module():
    main = (ROOT / "main.tf").read_text()
    (block,) = re.findall(r'module "management_routes" \{(.*?)\n\}', main, re.DOTALL)
    source = re.search(r'source\s*=\s*"([^"]+)"', block)
    assert source is not None
    assert (ROOT / source.group(1)).resolve() == ROUTES_MODULE
    # パスは本体スタックが作ったものを REST API から引くため、resource_ids は渡さない
    passed_through = {
        "route_group": '"management"',
        "rest_api_id": "var.rest_api.id",
        "authorizer_id": "var.rest_api.authorizer_id",
        "authorization_scope": "var.rest_api.portal_api_scope",
        "lambda_invoke_arn": "data.aws_lambda_function.management.invoke_arn",
        "function_name": "var.management_function_name",
    }
    assert _variables(ROUTES_MODULE) - {"resource_ids"} == set(passed_through)
    for name, value in passed_through.items():
        assert re.search(rf"^\s*{name}\s*=\s*{re.escape(value)}$", block, re.MULTILINE), name
