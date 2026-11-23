"""決裁 Lambda の実行ロールが、決裁に必要な権限だけを持つことを検査する。"""

import json
import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "infrastructure/modules/decision"
ROOT = Path(__file__).resolve().parents[2] / "infrastructure/decision"
# 本体スタックと決裁で共通の、発見用 M2M client の設定（環境変数と KMS の文）
DISCOVERY_MODULE = Path(__file__).resolve().parents[2] / "infrastructure/modules/discovery_client"
MAIN_STACK = Path(__file__).resolve().parents[2] / "infrastructure"
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
        "aws_iam_role",
        "aws_iam_role_policy",
        # 発見用 secret が顧客管理キーの場合だけ付ける kms:Decrypt（#49）
        "aws_iam_role_policy",
        "aws_lambda_function",
    ]
    # ロググループと発見用 M2M の設定は、本体スタックと共通のモジュールで作る
    assert re.findall(r'^module "([^"]+)" \{\n\s*source\s*=\s*"([^"]+)"', main, re.MULTILINE) == [
        ("log_group", "../lambda_log_group"),
        ("discovery_client", "../discovery_client"),
        ("decision_routes", "../api_routes"),
    ]
    # 実行ロールは決裁 Lambda 専用に新しく作る（管理 Lambda のロールを流用しない）
    assert 'Principal = { Service = "lambda.amazonaws.com" }' in main
    assert 'handler          = "registry_portal.handlers.curation.handler"' in main
    assert "aws_lambda_function_url" not in main
    for env in ("COGNITO_ISSUER", "HUMAN_CLIENT_ID", "REGISTRY_ARN", "SUPPLEMENT_TABLE_NAME"):
        assert env in main
    # 発見用 M2M の環境変数（DISCOVERY_*）は共通モジュールの出力から渡す
    assert "variables = merge(module.discovery_client.environment, {" in main
    discovery_outputs = (DISCOVERY_MODULE / "outputs.tf").read_text()
    for env in ("DISCOVERY_TOKEN_URL", "DISCOVERY_CLIENT_ID", "DISCOVERY_CLIENT_SECRET_ARN"):
        assert env in discovery_outputs


def test_module_routes_only_curator_and_recovery_apis_to_the_decision_lambda():
    """経路表の決裁の経路を、決裁 Lambda に割り当てる。

    経路の中身は、決裁 Lambda のルート表と照合する（tests/test_route_tables.py）。
    """
    main = (MODULE / "main.tf").read_text()
    (wiring,) = re.findall(r'module "decision_routes" \{(.*?)\n\}', main, re.DOTALL)
    assert re.search(r'route_group\s*=\s*"decision"', wiring)
    assert re.search(r"lambda_invoke_arn\s*=\s*aws_lambda_function\.decision\.invoke_arn", wiring)
    assert re.search(r"function_name\s*=\s*aws_lambda_function\.decision\.function_name", wiring)
    assert re.search(r"authorization_scope\s*=\s*var\.rest_api\.portal_api_scope", wiring)


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
    # policy は本体スタックと共通のモジュールが描画する
    assert re.search(r"^\s*policy\s*=\s*module\.discovery_client\.kms_policy$", block, re.M)
    main = (MODULE / "main.tf").read_text()
    (module_block,) = re.findall(r'^module "discovery_client" \{(.*?)\n\}', main, re.DOTALL | re.M)
    assert re.search(r"^\s*kms_key_arn\s*=\s*var\.discovery_kms_key_arn$", module_block, re.M)
    assert re.search(r"^\s*client\s*=\s*var\.discovery_client$", module_block, re.M)
    # 描画に渡す値は、キー ARN と secret ARN、secret の ARN から導出したリージョン
    discovery_main = (DISCOVERY_MODULE / "main.tf").read_text()
    assert re.search(r"^\s*kms_key_arn\s*=\s*var\.kms_key_arn$", discovery_main, re.M)
    assert re.search(
        r"^\s*discovery_secret_arn\s*=\s*var\.client\.client_secret_arn$", discovery_main, re.M
    )
    assert re.search(
        r'^\s*secret_region\s*=\s*split\(":", var\.client\.client_secret_arn\)\[3\]$',
        discovery_main,
        re.M,
    )
    (template_name,) = re.findall(r'templatefile\("\$\{path\.module\}/([^"]+)"', discovery_main)
    rendered = (
        (DISCOVERY_MODULE / template_name)
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
    # SecretARN は対象 secret との完全一致（StringEquals）。StringLike だとワイルドカードで複数の secret に広がる
    assert statements == [
        {
            "Effect": "Allow",
            "Action": ["kms:Decrypt"],
            "Resource": KMS_KEY_ARN,
            "Condition": {
                "StringEquals": {
                    "kms:ViaService": "secretsmanager.us-east-1.amazonaws.com",
                    "kms:EncryptionContext:SecretARN": DISCOVERY_SECRET_ARN,
                },
            },
        }
    ]


def test_discovery_kms_key_arn_is_an_optional_full_key_arn_in_both_modules():
    """ルートとモジュールの両方に、既定 null で完全なキー ARN だけを受け付ける変数がある。

    #49 の受入条件により decision のルートとモジュールの両方で検証し、本体スタックも同じ正規表現を使う。
    Terraform の検証は定数を共有できないため、3 か所の正規表現が同じであることをここで確かめる。
    """
    patterns = set()
    for variables_path in (
        ROOT / "variables.tf",
        MODULE / "variables.tf",
        MAIN_STACK / "variables.tf",
    ):
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
        patterns.add(pattern)
        assert re.fullmatch(pattern, KMS_KEY_ARN)
        assert re.fullmatch(pattern, "arn:aws:kms:ap-northeast-1:123456789012:key/mrk-0123abcd")
        for invalid in (
            "arn:aws:kms:us-east-1:123456789012:alias/aws/secretsmanager",
            "arn:aws:kms:us-east-1:123456789012:key/*",
            "00000000-0000-0000-0000-000000000000",
            "*",
        ):
            assert not re.fullmatch(pattern, invalid), invalid
    assert len(patterns) == 1, patterns
