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
    # 管理の route 表（routes）はルートモジュールが持ち、scope は本体スタックの output を転記した変数で受け取る
    routes_inputs = (_variables(ROUTES_MODULE) - {"routes", "authorization_scope"}) | {
        "portal_api_scope"
    }
    assert root_variables == module_variables | routes_inputs | {"region"}
    # CI では検証だけを行うため、state の保存先は部分設定にして環境ごとに渡す
    assert 'backend "s3" {}' in (ROOT / "versions.tf").read_text()


ROUTES_MODULE = MODULE.parent / "management_routes"


def _variables(module: Path) -> set[str]:
    return set(
        re.findall(r'^variable "([^"]+)"', (module / "variables.tf").read_text(), re.MULTILINE)
    )


def test_routes_module_assigns_only_portal_added_management_routes():
    """Registry 管理・履歴・廃止依頼・通知・下書きの route を、既存の管理 Lambda の統合に割り当てる。

    HTTP API・authorizer・統合は借用し、作らない。呼び出し許可は割り当てた path だけに限る。
    route 表は management のルートモジュールが持ち、route と呼び出し許可は modules/management_routes が作る。
    """
    route_table = (ROOT / "main.tf").read_text()
    main = (ROUTES_MODULE / "main.tf").read_text()
    assert re.findall(r'route_key\s*=\s*"([^"]+)"', route_table) == [
        "GET /api/admin/records",
        "GET /api/admin/connection",
        "GET /api/records/{recordId}/history",
        "POST /api/records/{recordId}/deprecation-requests",
        "GET /api/notifications",
        "POST /api/notifications/read",
        "POST /api/records",
        "GET /api/records",
        "GET /api/records/{recordId}",
        "PATCH /api/records/{recordId}",
        "GET /api/operations/{operationId}",
        "POST /api/records/{recordId}/submit",
    ]
    assert re.findall(r'^resource "([^"]+)"', main, re.MULTILINE) == [
        "aws_apigatewayv2_route",
        "aws_lambda_permission",
    ]
    assert re.search(r'target\s+= "integrations/\$\{var\.management_integration_id\}"', main)
    assert re.search(r'authorization_type\s+= "JWT"', main)
    assert re.search(r"authorizer_id\s+= var\.jwt_authorizer_id", main)
    # 本体スタックの route と同じポータル用のカスタム scope（呼び出し元が渡す）を JWT authorizer で検証する
    assert re.search(r"authorization_scopes = \[var\.authorization_scope\]", main)
    assert re.search(r"for_each\s+= var\.routes", main)
    # 呼び出し許可の path は route_key から導き、map に別の値として持たない。
    # 先頭の / を除き、パスパラメータを * にする（Terraform の式と同じ規則で期待値を確かめる）
    assert re.findall(r'path\s*=\s*"([^"]+)"', main) == []
    assert (
        'name => "${split(" ", route.route_key)[0]}/'
        '${replace(trimprefix(split(" ", route.route_key)[1], "/"), "/\\\\{[^}]+\\\\}/", "*")}"'
    ) in main
    route_keys = re.findall(r'route_key\s*=\s*"([^"]+)"', route_table)
    paths = [
        re.sub(r"\{[^}]+\}", "*", route_key.split(" ")[1].removeprefix("/"))
        for route_key in route_keys
    ]
    assert paths == [
        "api/admin/records",
        "api/admin/connection",
        "api/records/*/history",
        "api/records/*/deprecation-requests",
        "api/notifications",
        "api/notifications/read",
        "api/records",
        "api/records",
        "api/records/*",
        "api/records/*",
        "api/operations/*",
        "api/records/*/submit",
    ]
    # stage だけを * にし、メソッドと path は route ごとの値（local.invoke_paths）に限る
    (source_arn,) = re.findall(r"^\s*source_arn\s*=\s*(.+)$", main, re.MULTILINE)
    assert source_arn == (
        '"${data.aws_apigatewayv2_api.portal.execution_arn}/*/${local.invoke_paths[each.key]}"'
    )


def test_root_module_applies_the_routes_module():
    main = (ROOT / "main.tf").read_text()
    (block,) = re.findall(r'module "management_routes" \{(.*?)\n\}', main, re.DOTALL)
    source = re.search(r'source\s*=\s*"([^"]+)"', block)
    assert source is not None
    assert (ROOT / source.group(1)).resolve() == ROUTES_MODULE
    passed_through = {
        "routes": "local.management_routes",
        "authorization_scope": "var.portal_api_scope",
    }
    for name in _variables(ROUTES_MODULE):
        value = re.escape(passed_through.get(name, f"var.{name}"))
        assert re.search(rf"^\s*{name}\s*=\s*{value}$", block, re.MULTILINE), name
