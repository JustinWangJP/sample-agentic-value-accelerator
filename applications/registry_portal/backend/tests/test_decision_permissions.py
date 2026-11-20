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
        # 発見用 secret が顧客管理キーの場合だけ付ける kms:Decrypt（#49）
        "aws_iam_role_policy",
        "aws_lambda_function",
        "aws_apigatewayv2_integration",
        "aws_apigatewayv2_route",
        # 審査と決裁、/api/records/ 以下の path（for_each。管理レコードの検索を含む）、回復の path
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
    """審査・決裁・公開管理・見送り・管理レコードの検索と Admin の回復の route だけを決裁 Lambda に割り当てる。

    既存の JWT authorizer を適用し、HTTP API と authorizer は作らない。
    """
    main = (MODULE / "main.tf").read_text()
    (paths,) = re.findall(r"decision_record_paths = \{(.*?)\n  \}\n", main, re.DOTALL)
    # /api/records/ 以下の path は、静的な管理レコードの検索も含めて、route と呼び出し許可の両方を
    # 同じ定義から作る
    record_paths = re.findall(r'method\s*=\s*"([A-Z]+)"\s*\n\s*path\s*=\s*"([^"]+)"', paths)
    assert record_paths == [
        ("POST", "{recordId}/adopt"),
        ("POST", "{recordId}/reassign-owner"),
        ("POST", "{recordId}/suspend"),
        ("POST", "{recordId}/deprecate"),
        ("GET", "{recordId}/publication"),
        ("GET", "publication"),
        ("POST", "{recordId}/deprecation-requests/decline"),
    ]
    (routes,) = re.findall(r"routes = concat\((.*?)\n  \)\n", main, re.DOTALL)
    assert (
        '"${entry.method} /api/records/${entry.path}"' in routes
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
    assert re.search(r'authorization_type\s+= "JWT"', main)
    assert re.search(r"authorizer_id\s+= var\.jwt_authorizer_id", main)
    # 本体スタックの route と同じポータル用のカスタム scope を JWT authorizer で検証する
    assert re.search(r'authorization_scopes = \["registry-portal/api\.invoke"\]', main)
    assert 'payload_format_version = "2.0"' in main
    # HTTP API と authorizer は借用し、作成しない
    assert 'resource "aws_apigatewayv2_api"' not in main
    assert 'resource "aws_apigatewayv2_authorizer"' not in main
    # 呼び出し許可は審査と決裁、/api/records/ 以下の path、回復の path だけに限る。
    # {recordId} はワイルドカードに置き換え、静的な検索の path は完全一致で許可する
    source_arns = re.findall(r'source_arn\s*=\s*"(.+)"$', main, re.MULTILINE)
    assert [arn.split("/*/*")[1] for arn in source_arns] == [
        "/api/reviews*",
        '/api/records/${replace(each.value.path, "{recordId}", "*")}',
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


KMS_KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
DISCOVERY_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"


def _discovery_kms_policy_block() -> str:
    main = (MODULE / "main.tf").read_text()
    (block,) = re.findall(
        r'^resource "aws_iam_role_policy" "decision_discovery_kms" \{(.*?)\n\}',
        main,
        re.DOTALL | re.MULTILINE,
    )
    return block


def test_discovery_kms_decrypt_is_not_granted_when_key_arn_is_null():
    """顧客管理キーを指定しない（null）とき、決裁 Lambda の実行ロールに KMS の権限が増えない（#49）。"""
    # 常に付く基本の policy には KMS の action を含めない
    assert not any(
        action.startswith("kms:")
        for statement in render_policy()["Statement"]
        for action in statement["Action"]
    )
    # KMS の policy は別の資源にし、キー ARN が null なら作らない
    block = _discovery_kms_policy_block()
    assert re.search(r"^\s*count\s*=\s*var\.discovery_kms_key_arn == null \? 0 : 1$", block, re.M)
    assert re.search(r"^\s*role\s*=\s*aws_iam_role\.decision\.name$", block, re.M)


def test_discovery_kms_decrypt_only_allows_the_key_via_secrets_manager_for_the_secret():
    """キー ARN を指定したとき、そのキーへの kms:Decrypt だけを、本体スタックと同じ 2 つの条件付きで許可する。"""
    block = _discovery_kms_policy_block()
    # 描画に渡す値は、キー ARN と secret ARN、secret の ARN から導出したリージョン
    assert re.search(r"^\s*kms_key_arn\s*=\s*var\.discovery_kms_key_arn$", block, re.M)
    assert re.search(
        r"^\s*discovery_secret_arn\s*=\s*var\.discovery_client_secret_arn$", block, re.M
    )
    assert re.search(
        r'^\s*secret_region\s*=\s*split\(":", var\.discovery_client_secret_arn\)\[3\]$',
        block,
        re.M,
    )
    (template_name,) = re.findall(r'templatefile\("\$\{path\.module\}/([^"]+)"', block)
    rendered = (
        (MODULE / template_name)
        .read_text()
        .replace("${kms_key_arn}", KMS_KEY_ARN)
        .replace("${discovery_secret_arn}", DISCOVERY_SECRET_ARN)
        .replace("${secret_region}", "us-east-1")
    )
    assert "${" not in rendered
    statements = [
        {key: value for key, value in statement.items() if key != "Sid"}
        for statement in json.loads(rendered)["Statement"]
    ]
    assert statements == [
        {
            "Effect": "Allow",
            "Action": ["kms:Decrypt"],
            "Resource": KMS_KEY_ARN,
            "Condition": {
                "StringEquals": {"kms:ViaService": "secretsmanager.us-east-1.amazonaws.com"},
                "StringLike": {"kms:EncryptionContext:SecretARN": DISCOVERY_SECRET_ARN},
            },
        }
    ]


def test_discovery_kms_key_arn_is_an_optional_full_key_arn_in_both_modules():
    """ルートとモジュールの両方に、既定 null で完全なキー ARN だけを受け付ける変数がある。"""
    for variables_path in (ROOT / "variables.tf", MODULE / "variables.tf"):
        variables = variables_path.read_text()
        (block,) = re.findall(
            r'^variable "discovery_kms_key_arn" \{(.*?)\n\}', variables, re.DOTALL | re.M
        )
        assert re.search(r"^\s*type\s*=\s*string$", block, re.M), variables_path
        assert re.search(r"^\s*default\s*=\s*null$", block, re.M), variables_path
        assert re.search(r'^\s*description\s*=\s*".+"$', block, re.M), variables_path
        assert "var.discovery_kms_key_arn == null ? true :" in block, variables_path
        (pattern,) = re.findall(r'regex\("([^"]+)"', block)
        pattern = pattern.replace("\\\\", "\\")
        assert re.fullmatch(pattern, KMS_KEY_ARN)
        assert re.fullmatch(pattern, "arn:aws:kms:ap-northeast-1:123456789012:key/mrk-0123abcd")
        for invalid in (
            "arn:aws:kms:us-east-1:123456789012:alias/aws/secretsmanager",
            "arn:aws:kms:us-east-1:123456789012:key/*",
            "00000000-0000-0000-0000-000000000000",
            "*",
        ):
            assert not re.fullmatch(pattern, invalid), invalid
