"""決裁 Lambda の実行ロールが、決裁に必要な権限だけを持つことを検査する。"""

import json
import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "infrastructure/modules/decision"
ROOT = Path(__file__).resolve().parents[2] / "infrastructure/decision"
REGISTRY_ARN = "arn:aws:agent-registry:ap-northeast-1:123456789012:registry/abcdefghijkl"
TABLE_ARN = "arn:aws:dynamodb:ap-northeast-1:123456789012:table/registry-portal-supplement"
LOG_GROUP_ARN = (
    "arn:aws:logs:ap-northeast-1:123456789012:log-group:/aws/lambda/registry-portal-decision"
)


def render_policy() -> dict:
    """templatefile と同じく、テンプレートの変数を置き換えて JSON として読む。"""
    template = (MODULE / "decision-policy.json.tftpl").read_text()
    rendered = (
        template.replace("${registry_arn}", REGISTRY_ARN)
        .replace("${supplement_table_arn}", TABLE_ARN)
        .replace("${log_group_arn}", LOG_GROUP_ARN)
        .replace(
            "${discovery_secret_arn}",
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery",
        )
    )
    assert "${" not in rendered
    return json.loads(rendered)


def test_decision_policy_only_grants_status_transitions_reads_and_supplement_writes():
    statements = {statement["Sid"]: statement for statement in render_policy()["Statement"]}
    assert statements["RegistryDecisions"] == {
        "Sid": "RegistryDecisions",
        "Effect": "Allow",
        "Action": [
            "agent-registry:GetRegistryRecord",
            "agent-registry:ListRegistryRecords",
            "agent-registry:GetRegistry",
            "agent-registry:UpdateRegistryRecordStatus",
        ],
        "Resource": [REGISTRY_ARN, f"{REGISTRY_ARN}/record/*"],
    }
    assert statements["DecisionSupplement"] == {
        "Sid": "DecisionSupplement",
        "Effect": "Allow",
        # Query は審査待ちの一覧が未解決の廃止依頼（#13）を読むために使う。ConditionCheckItem は
        # 廃止依頼の見送りが、ロックがないことをトランザクションの中で確かめるために使う
        "Action": [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:DeleteItem",
            "dynamodb:Query",
            "dynamodb:ConditionCheckItem",
        ],
        "Resource": TABLE_ARN,
    }
    assert statements["DecisionLogs"]["Resource"] == f"{LOG_GROUP_ARN}:*"


def test_decision_policy_never_grants_descriptor_writes_or_submissions():
    """決裁 handler は descriptor の作成・更新や申請をできない（設計書「決裁 Lambda」）。"""
    rendered = (MODULE / "decision-policy.json.tftpl").read_text()
    for forbidden in (
        "CreateRegistryRecord",
        'UpdateRegistryRecord"',
        "SubmitRegistryRecordForApproval",
        "DeleteRegistryRecord",
        'UpdateRegistry"',
        'DeleteRegistry"',
        'CreateRegistry"',
        "iam:PassRole",
    ):
        assert forbidden not in rendered, f"決裁ロールに {forbidden} を与えてはいけません"


def test_module_creates_the_decision_lambda_with_its_own_role_and_log_group():
    main = (MODULE / "main.tf").read_text()
    assert re.findall(r'^resource "([^"]+)"', main, re.MULTILINE) == [
        "aws_cloudwatch_log_group",
        "aws_iam_role",
        "aws_iam_role_policy",
        "aws_lambda_function",
        "aws_apigatewayv2_integration",
        "aws_apigatewayv2_route",
        # 審査と決裁、Curator のレコード単位の path（for_each）、回復の path
        "aws_lambda_permission",
        "aws_lambda_permission",
        "aws_lambda_permission",
    ]
    # 実行ロールは決裁 Lambda 専用に新しく作る（管理 Lambda のロールを流用しない）
    assert 'Principal = { Service = "lambda.amazonaws.com" }' in main
    assert 'handler          = "registry_portal.handlers.curation.handler"' in main
    assert "aws_lambda_function_url" not in main
    for env in ("COGNITO_ISSUER", "HUMAN_CLIENT_ID", "REGISTRY_ARN", "SUPPLEMENT_TABLE_NAME"):
        assert env in main
    # 補助テーブルがないと決裁 handler は起動しない（アプリ側の ConfigurationError と同じ前提）
    assert "DISCOVERY_TOKEN_URL" in main
    assert "DISCOVERY_CLIENT_SECRET_ARN" in main


def test_module_routes_only_curator_and_recovery_apis_to_the_decision_lambda():
    """審査・決裁・公開管理・見送りの route と Admin の回復の route だけを決裁 Lambda に割り当てる。

    既存の JWT authorizer を適用し、HTTP API と authorizer は作らない。
    """
    main = (MODULE / "main.tf").read_text()
    (paths,) = re.findall(r"decision_record_paths = \{(.*?)\n  \}\n", main, re.DOTALL)
    # Curator のレコード単位の path は、route と呼び出し許可の両方を同じ定義から作る
    curator_paths = re.findall(r'method\s*=\s*"([A-Z]+)"\s*\n\s*path\s*=\s*"([^"]+)"', paths)
    assert curator_paths == [
        ("POST", "adopt"),
        ("POST", "reassign-owner"),
        ("POST", "suspend"),
        ("POST", "deprecate"),
        ("GET", "publication"),
        ("POST", "deprecation-requests/decline"),
    ]
    (routes,) = re.findall(r"routes = concat\((.*?)\n  \)\n", main, re.DOTALL)
    assert (
        '"${entry.method} /api/records/{recordId}/${entry.path}"' in routes
        and "values(local.decision_record_paths)" in routes
    )
    assert re.findall(r'^\s*"([A-Z]+ /api/[^"]+)",$', routes, re.MULTILINE) == [
        "GET /api/reviews",
        "GET /api/reviews/{recordId}",
        "POST /api/reviews/{recordId}/approve",
        "POST /api/reviews/{recordId}/reject",
        "GET /api/records/{recordId}/recovery",
        "POST /api/records/{recordId}/recovery",
    ]
    assert 'authorization_type = "JWT"' in main
    assert "authorizer_id      = var.jwt_authorizer_id" in main
    assert 'payload_format_version = "2.0"' in main
    # HTTP API と authorizer は借用し、作成しない
    assert 'resource "aws_apigatewayv2_api"' not in main
    assert 'resource "aws_apigatewayv2_authorizer"' not in main
    # 呼び出し許可は審査と決裁、Curator のレコード単位の path、回復の path だけに限る
    source_arns = re.findall(r"source_arn\s*=\s*\"([^\"]+)\"", main)
    assert [arn.split("/*/*")[1] for arn in source_arns] == [
        "/api/reviews*",
        "/api/records/*/${each.value.path}",
        "/api/records/*/recovery",
    ]


def test_arn_validation_does_not_pin_a_region():
    variables = (MODULE / "variables.tf").read_text()
    assert "us-east-1" not in variables
    for arn in (REGISTRY_ARN, TABLE_ARN):
        assert any(
            re.fullmatch(pattern, arn) for pattern in re.findall(r'regex\("([^"]+)"', variables)
        )


def test_root_module_applies_the_decision_module():
    """決裁モジュールが、ルートモジュールから決裁 Lambda として適用される経路を持つ。"""
    main = (ROOT / "main.tf").read_text()
    (block,) = re.findall(r'module "decision" \{(.*?)\n\}', main, re.DOTALL)
    source = re.search(r'source\s*=\s*"([^"]+)"', block)
    assert source is not None
    assert (ROOT / source.group(1)).resolve() == MODULE
    module_variables = set(
        re.findall(r'^variable "([^"]+)"', (MODULE / "variables.tf").read_text(), re.MULTILINE)
    )
    for name in module_variables:
        if name == "cognito_issuer":
            assert "cognito-idp" in block
        else:
            assert re.search(rf"^\s*{name}\s*=\s*", block, re.MULTILINE), name
    # CI では検証だけを行うため、state の保存先は部分設定にして環境ごとに渡す
    assert 'backend "s3" {}' in (ROOT / "versions.tf").read_text()
