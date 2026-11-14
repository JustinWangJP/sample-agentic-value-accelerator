"""各 handler が補助テーブルに実際に送る DynamoDB の操作を、実行ロールのポリシーが許可しているかの検査。

ポリシーの文面の検査（test_*_permissions.py）だけでは、補助ストアがトランザクションで新しい種類の操作
（Update など）を使い始めても気づけない。実環境では AccessDenied になり、moto では再現しないため、
主要な書き込み（下書き・申請・送信時刻の記録・決裁と再送・廃止依頼・廃止・通知と既読）で送られる
操作を記録し、IaC のポリシーが許可する action に含まれることを確かめる。
"""

from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber
from moto import mock_aws

from catalog_helpers import REGISTRY_ARN
from decision_helpers import GET, RECORD, decide, setup_pending, stub_status_update
from draft_helpers import RECORD_ID, call, clients, event
from notification_helpers import event_handler, registry_event
from permission_helpers import (
    DECISION_POLICY,
    DRAFT_POLICY,
    EVENTS_POLICY,
    DynamoDbActions,
    granted_dynamodb_actions,
)

CURATOR = "registry-curator"
# 既読にする通知の位置（形式だけ正しいもの）
POSITION = "2026-11-14T00:00:00.000000Z#" + "0" * 64


def _stub_get(stub, status):
    stub.add_response("get_registry_record", {**RECORD, "status": status}, GET)


def _post(handler, path, body, *, groups, key=""):
    return call(handler, event("POST", path, body, groups=groups, sub="carol", key=key))


def _another_client():
    """同じ moto の補助テーブルを読み書きする、別の DynamoDB クライアント（handler ごとに記録を分ける）。"""
    return boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@mock_aws
def test_management_and_decision_writes_are_granted_by_their_role_policies():
    """管理 handler と決裁 handler の主要な書き込みが送る操作は、それぞれのロールのポリシーが許可する。"""
    registry, db = clients()
    recorded = DynamoDbActions(db)
    with Stubber(registry) as stub:
        # 管理 handler: 下書きの作成と申請（どちらも Registry を呼ぶ前に送信時刻を記録する）
        state = setup_pending(stub, registry, db)
        management = recorded.take()

        # 決裁 handler: 結果不明の公開承認と、同じ Idempotency-Key での再送（再送の前にも送信時刻を記録する）
        _stub_get(stub, "PENDING_APPROVAL")
        stub_status_update(stub, approved=True, error="InternalServerException")
        status, _ = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 202
        _stub_get(stub, "PENDING_APPROVAL")
        stub_status_update(stub, approved=True)
        _stub_get(stub, "APPROVED")
        status, _ = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 200
        decision = recorded.take()

        # 管理 handler: 廃止依頼
        _stub_get(stub, "APPROVED")
        status, _ = call(
            state["management"],
            event(
                "POST",
                f"/api/records/{RECORD_ID}/deprecation-requests",
                {"reason": "利用が終わったため"},
                key="",
            ),
        )
        assert status == 200
        management |= recorded.take()

        # 決裁 handler: 廃止（依頼の解決を含む）
        _stub_get(stub, "APPROVED")
        stub.add_response(
            "update_registry_record_status",
            {
                "registryArn": REGISTRY_ARN,
                "recordArn": f"{REGISTRY_ARN}/record/{RECORD_ID}",
                "recordId": RECORD_ID,
                "status": "DEPRECATED",
                "statusReason": "利用終了のため",
                "updatedAt": RECORD["updatedAt"],
            },
            {**GET, "status": "DEPRECATED", "statusReason": "利用終了のため"},
        )
        _stub_get(stub, "DEPRECATED")
        status, _ = _post(
            state["decision"],
            f"/api/records/{RECORD_ID}/deprecate",
            {"reason": "利用終了のため", "confirm": True},
            groups=CURATOR,
            key="deprecate-1",
        )
        assert status == 200
        decision |= recorded.take()

        # 管理 handler: 通知の一覧と既読
        status, _ = call(
            state["management"], event("GET", "/api/notifications", groups=CURATOR, key="")
        )
        assert status == 200
        status, _ = call(
            state["management"],
            event(
                "POST", "/api/notifications/read", {"readUpTo": POSITION}, groups=CURATOR, key=""
            ),
        )
        assert status == 200
        management |= recorded.take()
        stub.assert_no_pending_responses()

    # 記録が空のまま通る検査にしない（トランザクションの各種類が記録されている）
    assert {"dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:ConditionCheckItem"} <= management
    assert {"dynamodb:PutItem", "dynamodb:DeleteItem"} <= decision
    assert management <= granted_dynamodb_actions(DRAFT_POLICY)
    assert decision <= granted_dynamodb_actions(DECISION_POLICY)


@mock_aws
def test_event_handler_writes_are_granted_by_the_events_role_policy():
    """イベント処理 handler の照合（実行制御の送信時刻の読み取りを含む）と通知の書き込みが送る操作。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events_db = _another_client()
        recorded = DynamoDbActions(events_db)
        events = event_handler(registry, events_db)
        stub.add_response(
            "get_registry_record",
            {**RECORD, "status": "PENDING_APPROVAL", "updatedAt": datetime.now(timezone.utc)},
            GET,
        )
        events(registry_event("Pending Approval", event_id="ev-1"), None)
        stub.assert_no_pending_responses()
        assert state["submitted"]

    actions = recorded.take()
    assert "dynamodb:PutItem" in actions
    assert actions <= granted_dynamodb_actions(EVENTS_POLICY)
