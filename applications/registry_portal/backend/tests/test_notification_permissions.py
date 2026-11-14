"""イベント処理 Lambda・実行ロール・EventBridge ルールの IaC（#15）の検査。

- 実行ロールは Registry の読み取りと、補助テーブルの読み取り・通知の書き込みだけを持つ
- ルールは借用する Registry のレコードのイベントだけを、イベント処理 Lambda に流す
- 管理 handler の通知 API の route は、既存の管理 Lambda の統合に割り当てる
"""

import json
import re
from pathlib import Path

from registry_portal.notification_events import (
    EVENT_SOURCE,
    RECORD_EVENT_PREFIX,
    RECORD_EVENT_STATES,
)

INFRA = Path(__file__).resolve().parents[2] / "infrastructure"
MODULE = INFRA / "modules/notification_events"
ROOT = INFRA / "notification_events"
REGISTRY_ARN = "arn:aws:agent-registry:ap-northeast-1:123456789012:registry/abcdefghijkl"
TABLE_ARN = "arn:aws:dynamodb:ap-northeast-1:123456789012:table/registry-portal-supplement"
DLQ_ARN = "arn:aws:sqs:ap-northeast-1:123456789012:registry-portal-events-dlq"
LOG_GROUP_ARN = (
    "arn:aws:logs:ap-northeast-1:123456789012:log-group:/aws/lambda/registry-portal-events"
)


def render_policy() -> dict:
    """templatefile と同じく、テンプレートの変数を置き換えて JSON として読む。"""
    rendered = (
        (MODULE / "events-policy.json.tftpl")
        .read_text()
        .replace("${registry_arn}", REGISTRY_ARN)
        .replace("${supplement_table_arn}", TABLE_ARN)
        .replace("${log_group_arn}", LOG_GROUP_ARN)
        .replace("${dead_letter_queue_arn}", DLQ_ARN)
    )
    assert "${" not in rendered
    return json.loads(rendered)


def test_events_role_only_reads_the_registry_and_writes_notifications():
    assert render_policy()["Statement"] == [
        {
            "Sid": "EventsLogs",
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": f"{LOG_GROUP_ARN}:*",
        },
        {
            "Sid": "RegistryRead",
            "Effect": "Allow",
            "Action": ["agent-registry:GetRegistryRecord"],
            "Resource": [REGISTRY_ARN, f"{REGISTRY_ARN}/record/*"],
        },
        {
            # 管理情報・操作記録・公開版スナップショット・逆引き・処理済みの印の読み取り
            "Sid": "SupplementRead",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:Query"],
            "Resource": TABLE_ARN,
        },
        {
            # 書き込みは通知・重複除去の印・処理済みのイベントのパーティションキーだけに限る
            "Sid": "NotificationWrite",
            "Effect": "Allow",
            "Action": ["dynamodb:PutItem"],
            "Resource": TABLE_ARN,
            "Condition": {
                "ForAllValues:StringLike": {
                    "dynamodb:LeadingKeys": [
                        f"REGISTRY#{REGISTRY_ARN}#NOTIFICATION#*",
                        f"REGISTRY#{REGISTRY_ARN}#NOTIFIED#*",
                        f"REGISTRY#{REGISTRY_ARN}#EVENT#*",
                    ]
                }
            },
        },
        {
            "Sid": "EventsDeadLetter",
            "Effect": "Allow",
            "Action": ["sqs:SendMessage"],
            "Resource": DLQ_ARN,
        },
    ]


def test_module_creates_the_events_lambda_with_its_own_role_and_rule():
    main = (MODULE / "main.tf").read_text()
    assert re.findall(r'^resource "([^"]+)"', main, re.MULTILINE) == [
        "aws_cloudwatch_log_group",
        "aws_sqs_queue",
        "aws_iam_role",
        "aws_iam_role_policy",
        "aws_lambda_function",
        "aws_lambda_function_event_invoke_config",
        "aws_cloudwatch_event_rule",
        "aws_cloudwatch_event_target",
        "aws_lambda_permission",
    ]
    assert 'handler          = "registry_portal.handlers.events.handler"' in main
    assert 'Principal = { Service = "lambda.amazonaws.com" }' in main
    # API Gateway からは呼ばれず、認証設定も持たない
    assert "aws_lambda_function_url" not in main
    assert "COGNITO_ISSUER" not in main
    assert "REGISTRY_ARN          = var.registry_arn" in main
    assert "SUPPLEMENT_TABLE_NAME = var.supplement_table_name" in main
    assert 'principal     = "events.amazonaws.com"' in main
    # 再試行を使い切ったイベントは、暗号化した DLQ に残す
    assert "target_arn = aws_sqs_queue.events_dead_letter.arn" in main
    assert "sqs_managed_sse_enabled   = true" in main
    assert "source_arn    = aws_cloudwatch_event_rule.registry_records.arn" in main


def test_rule_matches_only_record_events_of_the_borrowed_registry():
    """ルールの source と detail-type は、イベント処理 handler が受け付けるものと一致する。"""
    main = (MODULE / "main.tf").read_text()
    assert f'source        = ["{EVENT_SOURCE}"]' in main
    assert re.findall(r'^\s*"(Registry Record State changed to [^"]+)",$', main, re.MULTILINE) == [
        f"{RECORD_EVENT_PREFIX}{state}" for state in RECORD_EVENT_STATES
    ]
    assert "detail        = { registryId = [local.registry_id] }" in main
    assert 'registry_id = element(split("/", var.registry_arn), 1)' in main
    assert main.count('event_bus_name = "default"') == 2


def test_arn_validation_does_not_pin_a_region():
    variables = (MODULE / "variables.tf").read_text()
    assert "us-east-1" not in variables
    for arn in (REGISTRY_ARN, TABLE_ARN):
        assert any(
            re.fullmatch(pattern, arn) for pattern in re.findall(r'regex\("([^"]+)"', variables)
        )


def test_root_module_applies_the_events_module():
    main = (ROOT / "main.tf").read_text()
    (block,) = re.findall(r'module "notification_events" \{(.*?)\n\}', main, re.DOTALL)
    source = re.search(r'source\s*=\s*"([^"]+)"', block)
    assert source is not None
    assert (ROOT / source.group(1)).resolve() == MODULE
    module_variables = set(
        re.findall(r'^variable "([^"]+)"', (MODULE / "variables.tf").read_text(), re.MULTILINE)
    )
    for name in module_variables:
        assert re.search(rf"^\s*{name}\s*=\s*var\.{name}$", block, re.MULTILINE), name
    root_variables = set(
        re.findall(r'^variable "([^"]+)"', (ROOT / "variables.tf").read_text(), re.MULTILINE)
    )
    assert root_variables == module_variables | {"region"}
    assert 'backend "s3" {}' in (ROOT / "versions.tf").read_text()


def test_notification_write_keys_match_the_store_keys():
    """IAM で書き込みを許すパーティションキーは、通知の補助ストアが書くキーと一致する。"""
    from registry_portal.domain.notifications import Notification
    from registry_portal.services.notification_store import NotificationStore

    class Recorder:
        def __init__(self):
            self.items = []

        def transact_write_items(self, *, TransactItems):
            self.items.extend(TransactItems)
            return {}

    client = Recorder()
    store = NotificationStore("table", REGISTRY_ARN, client)
    notification = Notification.create(
        "TRANSITION#x", "CURATOR", "SUBMITTED", "agentABC1234", None, "2026-11-14T00:00:00+00:00"
    )
    store.publish(notification, "TRANSITION#x")
    store.mark_event_processed("ev-1")
    patterns = render_policy()["Statement"][3]["Condition"]["ForAllValues:StringLike"][
        "dynamodb:LeadingKeys"
    ]
    keys = [item["Put"]["Item"]["pk"]["S"] for item in client.items]
    assert len(keys) == 3
    for key in keys:
        assert any(re.fullmatch(re.escape(p).replace(r"\*", ".*"), key) for p in patterns), key
