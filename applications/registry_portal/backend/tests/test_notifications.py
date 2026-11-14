"""画面上の通知（#15）の Seam 1 テスト。

- Registry の EventBridge イベントを、イベント処理 handler が受け取り、Registry を再取得して通知を作る
- 通知は管理 handler の GET /api/notifications で、宛先グループに属する主体が読む
"""

import time
from collections import deque
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from catalog_helpers import (
    ENV,
    ISSUER,
    REGISTRY_ARN,
    SUPPLEMENT_TABLE,
    FakeSecrets,
    FakeTransport,
    json_response,
    summary,
)
from decision_helpers import (
    CURATOR,
    GET,
    META,
    REASON,
    RECORD,
    decide,
    missing_published,
    put,
    setup_pending,
    stored,
    stub_status_update,
    stub_submit,
)
from draft_helpers import RECORD_ARN, RECORD_ID, call, clients, event, handler_for
from notification_helpers import event_handler, registry_event
from record_fixtures import CUSTOM_DESCRIPTORS, CUSTOM_DRAFT, stub_create
from registry_portal import recovery
from registry_portal.app import (
    create_curation_handler,
    create_event_handler,
    create_management_handler,
)
from registry_portal.config import ConfigurationError
from registry_portal.services import supplement_store
from registry_portal.services.discovery import DiscoveryDependencies

PUBLISHER = "registry-publisher team-payments"


def _now():
    return datetime.now(timezone.utc)


def _notifications(handler, groups, sub="dave"):
    return call(handler, event("GET", "/api/notifications", groups=groups, sub=sub, key=""))


@mock_aws
def test_pending_approval_event_notifies_curators_once():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        stub.add_response(
            "get_registry_record",
            {**RECORD, "status": "PENDING_APPROVAL", "updatedAt": _now()},
            GET,
        )
        events(registry_event("Pending Approval", event_id="ev-1"), None)
        stub.assert_no_pending_responses()

        status, body = _notifications(state["management"], CURATOR)
        assert status == 200
        assert body["unreadCount"] == 1
        (item,) = body["items"]
        assert item["kind"] == "SUBMITTED"
        assert item["recordId"] == RECORD_ID
        assert item["recordName"] == RECORD["displayName"]
        assert item["read"] is False
        assert "申請" in item["message"]

        # 同じ event ID の再配信は Registry を読み直さず、通知も増やさない
        events(registry_event("Pending Approval", event_id="ev-1"), None)
        stub.assert_no_pending_responses()
        assert _notifications(state["management"], CURATOR)[1]["unreadCount"] == 1

        # 宛先グループに属さない主体（所有チームの Publisher）には届かない
        status, body = _notifications(state["management"], PUBLISHER)
        assert (status, body["items"], body["unreadCount"]) == (200, [], 0)


def _approve(stub, state, *, approved=True):
    """審査待ちのレコードを Curator（carol）が決裁する。"""
    stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
    stub_status_update(stub, approved=approved, reason=REASON)
    target = "APPROVED" if approved else "REJECTED"
    stub.add_response("get_registry_record", {**RECORD, "status": target}, GET)
    status, _ = decide(state["decision"], approved, snapshot_hash=state["snapshot"]["snapshotHash"])
    assert status == 200


def _deliver(stub, handler, state, event_id, *, status, updated_at, **changes):
    """Registry を再取得した結果（status・updatedAt）を積んでから、イベントを1件届ける。"""
    stub.add_response(
        "get_registry_record",
        {**RECORD, "status": status, "updatedAt": updated_at, **changes},
        GET,
    )
    return handler(registry_event(state, event_id=event_id), None)


@pytest.mark.parametrize("approved, kind", [(True, "APPROVED"), (False, "REJECTED")])
@mock_aws
def test_approval_and_rejection_notify_only_the_owner_team_publishers(approved, kind):
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _approve(stub, state, approved=approved)
        result = _deliver(
            stub,
            events,
            "Approved" if approved else "Rejected",
            "ev-2",
            status=kind,
            updated_at=_now(),
            statusReason=REASON,
        )
        assert result == {"result": "PROCESSED", "notified": 1}
        stub.assert_no_pending_responses()

        (item,) = _notifications(state["management"], PUBLISHER)[1]["items"]
        assert item["kind"] == kind
        assert (REASON in item["message"]) is not approved
        # 他チームの Publisher・Curator には届かない
        assert (
            _notifications(state["management"], "registry-publisher team-other")[1]["items"] == []
        )
        assert _notifications(state["management"], CURATOR)[1]["items"] == []


@mock_aws
def test_events_out_of_order_use_the_refetched_state_and_notify_each_change_once():
    """申請のイベントが承認の後に届いても、再取得した状態（承認）として1回だけ通知する。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _approve(stub, state)
        approved_at = _now()
        _deliver(stub, events, "Approved", "ev-approved", status="APPROVED", updated_at=approved_at)
        # 遅れて届いた申請のイベント。別の event ID でも、再取得した状態変化は同じ
        late = _deliver(
            stub,
            events,
            "Pending Approval",
            "ev-pending",
            status="APPROVED",
            updated_at=approved_at,
        )
        assert late == {"result": "PROCESSED", "notified": 0}
        stub.assert_no_pending_responses()

        body = _notifications(state["management"], PUBLISHER)[1]
        assert [item["kind"] for item in body["items"]] == ["APPROVED"]
        assert body["unreadCount"] == 1
        # 審査が済んだ申請は、Curator に通知しない
        assert _notifications(state["management"], CURATOR)[1]["items"] == []


ADMIN = "registry-admin"


@mock_aws
def test_outside_change_reaches_admins_and_the_owner_team_but_not_curators():
    """申請だけの記録しかないのに承認された（ポータル外での直接承認）。

    Admin と所有チームの Publisher に「ポータル外の変更」として届く。所有チームには通常の公開承認と
    同じ種別にはせず、Curator にも届けない（確認されていない変化を通常の決裁と同じに見せない）。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _deliver(stub, events, "Approved", "ev-outside", status="APPROVED", updated_at=_now())
        stub.assert_no_pending_responses()

        (item,) = _notifications(state["management"], ADMIN)[1]["items"]
        assert item["kind"] == "OUTSIDE_CHANGE"
        assert "ポータルの外" in item["message"] and "APPROVED" in item["message"]
        (owner,) = _notifications(state["management"], PUBLISHER)[1]["items"]
        assert owner["kind"] == "OUTSIDE_CHANGE"
        assert "Admin" in owner["message"]
        # 宛先グループごとに別の通知になる（同じ状態変化でも、宛先ごとに1件ずつ）
        assert owner["notificationId"] != item["notificationId"]
        assert _notifications(state["management"], CURATOR)[1]["items"] == []
        assert (
            _notifications(state["management"], "registry-publisher team-other")[1]["items"] == []
        )


@mock_aws
def test_an_earlier_portal_operation_of_the_same_kind_does_not_explain_a_later_change():
    """ポータルで承認した後、時間をおいて再び承認された変化（ポータル外）は、過去の承認では説明しない。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _approve(stub, state)
        _deliver(
            stub,
            events,
            "Approved",
            "ev-later",
            status="APPROVED",
            updated_at=_now() + timedelta(hours=1),
        )
        stub.assert_no_pending_responses()
        assert [
            item["kind"] for item in _notifications(state["management"], ADMIN)[1]["items"]
        ] == ["OUTSIDE_CHANGE"]
        # 所有チームにも、公開承認ではなくポータル外の変更として届く
        assert [
            item["kind"] for item in _notifications(state["management"], PUBLISHER)[1]["items"]
        ] == ["OUTSIDE_CHANGE"]


def _kinds(handler, groups):
    return [item["kind"] for item in _notifications(handler, groups)[1]["items"]]


def _stuck_approval(stub, state):
    """決裁の AWS 呼び出しが 5xx で結果不明になり、結果確認中のままロックが残る。"""
    stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
    stub_status_update(stub, approved=True, error="InternalServerException")
    status, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
    assert (status, pending["operationState"]) == (202, "VERIFYING")


@mock_aws
def test_a_same_kind_change_far_from_the_send_of_a_verifying_operation_is_an_outside_change():
    """結果確認中の決裁も、Registry への送信時刻の前後の変化だけをポータル内とみなす（#38）。

    送信の直後の承認は、結果不明だった決裁が届いたものとして所有チームに届く。送信から1時間後の承認は、
    ロックを持つ間にポータル外（break-glass 用ロールなど）で起きた同じ種別の遷移として Admin に届く。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _stuck_approval(stub, state)
        _deliver(stub, events, "Approved", "ev-landed", status="APPROVED", updated_at=_now())
        assert _kinds(state["management"], PUBLISHER) == ["APPROVED"]
        assert _kinds(state["management"], ADMIN) == []

        _deliver(
            stub,
            events,
            "Approved",
            "ev-outside",
            status="APPROVED",
            updated_at=_now() + timedelta(hours=1),
        )
        stub.assert_no_pending_responses()
        assert _kinds(state["management"], ADMIN) == ["OUTSIDE_CHANGE"]
        assert _kinds(state["management"], PUBLISHER) == ["OUTSIDE_CHANGE", "APPROVED"]


def _later(monkeypatch, delay):
    """補助テーブルに記録する送信時刻・確定時刻を、操作の開始から delay 後にする。"""
    later = _now() + delay
    monkeypatch.setattr(supplement_store, "_utcnow", lambda: later)
    return later


@mock_aws
def test_a_resent_decision_that_lands_long_after_the_start_is_a_portal_change(monkeypatch):
    """結果不明の決裁を1時間後に同じ Idempotency-Key で再送し、その送信で反映された承認はポータル内。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _stuck_approval(stub, state)
        resent_at = _later(monkeypatch, timedelta(hours=1))
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, _ = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 200

        _deliver(stub, events, "Approved", "ev-resent", status="APPROVED", updated_at=resent_at)
        stub.assert_no_pending_responses()
        assert _kinds(state["management"], PUBLISHER) == ["APPROVED"]
        assert _kinds(state["management"], ADMIN) == []


@mock_aws
def test_an_operation_settled_by_recovery_is_matched_by_its_send_time(monkeypatch):
    """回復操作（#12）で確定した決裁も、回復を実行した時刻ではなく送信時刻で照合する。

    結果不明の決裁の30分後にポータル外で承認され、1時間後に Admin が回復で成功と確定した。
    送信から離れた承認は、確定までの間に起きていても Admin に届く。送信の直後の承認はポータル内。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        sent_at = _now()
        _stuck_approval(stub, state)
        recovered_at = _later(monkeypatch, timedelta(hours=1))
        monkeypatch.setattr(recovery, "_utcnow", lambda: recovered_at)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, body = call(
            state["decision"],
            event(
                "POST",
                f"/api/records/{RECORD_ID}/recovery",
                {"reason": "Registry を確認した"},
                groups=ADMIN,
                sub="admin-1",
                key="",
            ),
        )
        assert (status, body["operationState"]) == (200, "SUCCEEDED")

        _deliver(
            stub,
            events,
            "Approved",
            "ev-outside",
            status="APPROVED",
            updated_at=sent_at + timedelta(minutes=30),
        )
        assert _kinds(state["management"], ADMIN) == ["OUTSIDE_CHANGE"]
        _deliver(stub, events, "Approved", "ev-landed", status="APPROVED", updated_at=sent_at)
        stub.assert_no_pending_responses()
        assert _kinds(state["management"], ADMIN) == ["OUTSIDE_CHANGE"]
        assert "APPROVED" in _kinds(state["management"], PUBLISHER)


@mock_aws
def test_a_verifying_operation_without_a_send_time_explains_only_fifteen_minutes_from_its_start():
    """送信時刻を記録する前の操作は、開始から15分（Lambda の上限）までの変化だけをポータル内とみなす。

    この変更より前に始まり、結果確認中のまま残った公開停止（INTENT だけで、実行制御に送信時刻がない）。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        events = event_handler(registry, db)
        started = _now()
        put(db, "MANAGEMENT", {"managed": True, "ownerTeam": "team-payments"})
        put(
            db,
            "AUDIT#op-legacy#INTENT",
            {
                "operationId": "op-legacy",
                "kind": "SUSPEND",
                "principal": {"issuer": ISSUER, "subject": "carol"},
                "startedAt": started.isoformat(),
                "ownerTeam": "team-payments",
                "recordId": RECORD_ID,
            },
        )
        management = handler_for(registry, db)
        _deliver(
            stub,
            events,
            "Rejected",
            "ev-landed",
            status="REJECTED",
            updated_at=started + timedelta(minutes=1),
        )
        assert _kinds(management, PUBLISHER) == ["SUSPENDED"]
        _deliver(
            stub,
            events,
            "Rejected",
            "ev-outside",
            status="REJECTED",
            updated_at=started + timedelta(hours=1),
        )
        stub.assert_no_pending_responses()
        assert _kinds(management, ADMIN) == ["OUTSIDE_CHANGE"]


def _draft(stub, registry, db):
    """申請前の下書きを作る。発見 API の偽物には、申請1回分の応答（公開中の版なし）を積む。"""
    transport = FakeTransport(registry=deque([missing_published()]))
    management = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        supplement_store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
    )
    stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
    status, created = call(management, event(body=CUSTOM_DRAFT))
    assert status == 201
    return management, created


def _registry_configuration(stub):
    """自動承認が無効な Registry の設定（申請・申請の再開の前に確かめる）。"""
    stub.add_response(
        "get_registry",
        {
            "name": "registry",
            "registryId": "abcdEFGH1234",
            "registryArn": REGISTRY_ARN,
            "status": "ACTIVE",
            "createdAt": RECORD["createdAt"],
            "updatedAt": RECORD["updatedAt"],
            "approvalConfiguration": {"autoApprovalRules": []},
        },
        {"registryId": REGISTRY_ARN},
    )


@mock_aws
def test_a_submission_resumed_long_after_the_start_is_a_portal_change(monkeypatch):
    """申請 API が結果不明のまま1時間後に操作照会で申請を再開し、その送信で審査待ちになった変化はポータル内。

    再開した申請は反映待ち（DRAFT のまま）で結果確認中だが、送信時刻の直後の変化なので Curator に届く。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        management, created = _draft(stub, registry, db)
        events = event_handler(registry, db)
        stub.add_response("get_registry_record", RECORD, GET)
        _registry_configuration(stub)
        stub.add_client_error(
            "submit_registry_record_for_approval", "InternalServerException", http_status_code=500
        )
        status, pending = call(
            management,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": created["contentHash"],
                    "reason": "利用者向けに公開する",
                    "relatedReferences": [],
                },
                key="submit-1",
            ),
        )
        assert (status, pending["operationState"]) == (202, "VERIFYING")

        resumed_at = _later(monkeypatch, timedelta(hours=1))
        stub.add_response("get_registry_record", RECORD, GET)
        _registry_configuration(stub)
        stub.add_response(
            "submit_registry_record_for_approval",
            {
                "registryArn": REGISTRY_ARN,
                "recordArn": RECORD_ARN,
                "recordId": RECORD_ID,
                "status": "PENDING_APPROVAL",
                "updatedAt": RECORD["updatedAt"],
            },
            GET,
        )
        stub.add_response("get_registry_record", RECORD, GET)
        lookup = event("GET", f"/api/operations/{pending['operationId']}")
        assert call(management, lookup)[0] == 202

        _deliver(
            stub,
            events,
            "Pending Approval",
            "ev-resumed",
            status="PENDING_APPROVAL",
            updated_at=resumed_at,
        )
        stub.assert_no_pending_responses()
        assert _kinds(management, CURATOR) == ["SUBMITTED"]
        assert _kinds(management, ADMIN) == []


@mock_aws
def test_a_create_resent_long_after_the_start_is_a_portal_change(monkeypatch):
    """作成 API が結果不明のまま、1時間後に同じ Idempotency-Key で再送した作成の下書きはポータル内（通知しない）。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        management = handler_for(registry, db)
        events = event_handler(registry, db)
        stub.add_client_error(
            "create_registry_record", "InternalServerException", http_status_code=500
        )
        status, pending = call(management, event(body=CUSTOM_DRAFT, key="create-1"))
        assert (status, pending["operationState"]) == (202, "VERIFYING")

        resent_at = _later(monkeypatch, timedelta(hours=1))
        stub_create(
            stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META, returned={**RECORD, "status": "CREATING"}
        )
        status, pending = call(management, event(body=CUSTOM_DRAFT, key="create-1"))
        assert (status, pending["operationState"]) == (202, "VERIFYING")

        _deliver(stub, events, "Draft", "ev-created", status="DRAFT", updated_at=resent_at)
        stub.assert_no_pending_responses()
        assert _kinds(management, ADMIN) == []
        assert _kinds(management, PUBLISHER) == []


@mock_aws
def test_an_edit_is_matched_by_its_send_time_not_its_start(monkeypatch):
    """開始の検査に時間がかかり、開始から14分後に送信した編集（結果不明）。

    送信から16分後の下書きへの変化は、開始から15分を過ぎていても送信の前後なのでポータル内（通知しない）。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        management, created = _draft(stub, registry, db)
        events = event_handler(registry, db)
        sent_at = _later(monkeypatch, timedelta(minutes=14))
        stub.add_response("get_registry_record", RECORD, GET)
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        changed = {**CUSTOM_DRAFT, "description": "結果確認を待つ説明"}
        status, pending = call(
            management,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": changed, "expectedContentHash": created["contentHash"]},
            ),
        )
        assert (status, pending["operationState"]) == (202, "VERIFYING")

        _deliver(
            stub,
            events,
            "Draft",
            "ev-edited",
            status="DRAFT",
            updated_at=sent_at + timedelta(minutes=16),
        )
        stub.assert_no_pending_responses()
        assert _kinds(management, ADMIN) == []


class _SendTimeDenied:
    """補助テーブルのクライアント。送信時刻（lastSentAt）を含む書き込みだけを AccessDenied で拒否する。

    実行ロールに書き込みの権限がない、などで送信時刻を記録できない状況を、DynamoDB の境界で再現する。
    """

    def __init__(self, client):
        self._client = client

    def transact_write_items(self, *, TransactItems):
        if any("lastSentAt" in item.get("Put", {}).get("Item", {}) for item in TransactItems):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "TransactWriteItems",
            )
        return self._client.transact_write_items(TransactItems=TransactItems)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _control(db, op_id):
    """操作の実行制御（OP#…/CONTROL）。"""
    item = db.get_item(
        TableName=SUPPLEMENT_TABLE,
        Key={"pk": {"S": f"OP#{op_id}"}, "sk": {"S": "CONTROL"}},
    ).get("Item")
    return None if item is None else {k: TypeDeserializer().deserialize(v) for k, v in item.items()}


@mock_aws
def test_a_create_whose_send_time_cannot_be_recorded_is_not_sent_to_the_registry():
    """送信時刻を記録できなければ、作成 API を呼ばずに結果確認中（202）を返す（#38）。

    Stubber には作成 API の応答を積まない。呼べば Stubber が未定義の呼び出しとして失敗させる。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        management = handler_for(registry, _SendTimeDenied(db))
        status, pending = call(management, event(body=CUSTOM_DRAFT, key="create-1"))
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        stub.assert_no_pending_responses()
        assert "lastSentAt" not in _control(db, pending["operationId"])


@mock_aws
def test_a_decision_whose_send_time_cannot_be_recorded_is_not_sent_to_the_registry():
    """送信時刻を記録できなければ、決裁の状態更新 API を呼ばずに結果確認中（202）を返す（#38）。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        decision = create_curation_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            store_client=_SendTimeDenied(db),
        )
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, pending = decide(decision, snapshot_hash=state["snapshot"]["snapshotHash"])
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        stub.assert_no_pending_responses()
        assert "lastSentAt" not in _control(db, pending["operationId"])


@mock_aws
def test_the_send_time_is_kept_only_in_the_execution_control():
    """送信時刻は実行制御にだけ記録し、操作記録（INTENT・RESULT と、作成の INTENT の写し）には書かない。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.assert_no_pending_responses()
    items = [
        {k: TypeDeserializer().deserialize(v) for k, v in item.items()}
        for item in db.scan(TableName=SUPPLEMENT_TABLE)["Items"]
    ]
    audits = [item for item in items if item["sk"].startswith("AUDIT#")]
    # 作成（名前のロック側の正本とレコード単位の写し）と申請の、それぞれの INTENT と RESULT
    assert len(audits) == 6
    assert all("lastSentAt" not in item for item in audits)
    assert "lastSentAt" in _control(db, state["submitted"]["submissionId"])


@pytest.mark.parametrize("broken", [12345, "", "送信時刻ではない"])
@mock_aws
def test_an_operation_with_a_broken_send_time_does_not_explain_a_change(broken):
    """実行制御の送信時刻の型・形式が崩れた操作は、照合に使わない（説明できない変化として届く）。

    送信時刻のない操作として開始からの窓に置き換えると、保存内容の異常をポータル内の変更として隠してしまう。
    """
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        _stuck_approval(stub, state)
        (op_id,) = [
            item["operationId"]["S"]
            for item in db.scan(TableName=SUPPLEMENT_TABLE)["Items"]
            if item["sk"]["S"] == "CONTROL" and item.get("kind", {}).get("S") == "APPROVE"
        ]
        db.update_item(
            TableName=SUPPLEMENT_TABLE,
            Key={"pk": {"S": f"OP#{op_id}"}, "sk": {"S": "CONTROL"}},
            UpdateExpression="SET lastSentAt = :broken",
            ExpressionAttributeValues={":broken": TypeSerializer().serialize(broken)},
        )
        _deliver(stub, events, "Approved", "ev-landed", status="APPROVED", updated_at=_now())
        stub.assert_no_pending_responses()
        assert _kinds(state["management"], ADMIN) == ["OUTSIDE_CHANGE"]
        assert _kinds(state["management"], PUBLISHER) == ["OUTSIDE_CHANGE"]


@mock_aws
def test_changes_to_unmanaged_records_reach_only_admins():
    """未管理レコードにはポータル上の所有チームがないため、Admin にだけ届く。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        events = event_handler(registry, db)
        other = "otherABC1234"
        stub.add_response(
            "get_registry_record",
            {**RECORD, "recordId": other, "status": "PENDING_APPROVAL", "updatedAt": _now()},
            {**GET, "recordId": other},
        )
        result = events(registry_event("Pending Approval", event_id="ev-1", record_id=other), None)
        assert result == {"result": "PROCESSED", "notified": 1}
        stub.assert_no_pending_responses()
        management = handler_for(registry, db)
        (item,) = _notifications(management, ADMIN)[1]["items"]
        assert (item["kind"], item["recordId"]) == ("OUTSIDE_CHANGE", other)
        # ポータル外の申請は Curator の審査待ちに通知しない（取り込みと再申請から進める）
        assert _notifications(management, CURATOR)[1]["items"] == []
        assert _notifications(management, PUBLISHER)[1]["items"] == []


SKILL_ID = "skillABC1234"
SKILL_GET = {**GET, "recordId": SKILL_ID}
SKILL = {**RECORD, "recordId": SKILL_ID, "recordType": "SKILL", "displayName": "決済スキル"}


def _approve_agent_referencing_skill(stub, registry, db):
    """Skill を関連参照に持つ Agent（team-payments）を、ポータルで申請・公開承認する。"""
    skill = {**summary(SKILL_ID, "SKILL", recordVersion="1.2.3"), "descriptors": CUSTOM_DESCRIPTORS}
    transport = FakeTransport(
        registry=deque(
            [missing_published(), json_response(200, {"registryRecords": [skill], "errors": []})]
        )
    )
    management = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        supplement_store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
    )
    decision = create_curation_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        store_client=db,
    )
    stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
    _, created = call(management, event(body=CUSTOM_DRAFT))
    stub_submit(stub)
    _, submitted = call(
        management,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/submit",
            {
                "expectedContentHash": created["contentHash"],
                "reason": "参照付きで公開する",
                "relatedReferences": [{"recordId": SKILL_ID, "recordVersion": "1.2.3"}],
            },
            sub="bob",
            key="submit-1",
        ),
    )
    snapshot = stored(db, f'SUBMISSION#{submitted["submissionId"]}')
    _approve(stub, {"decision": decision, "snapshot": snapshot})
    return management


@mock_aws
def test_outside_deprecation_of_a_reference_reaches_admins_and_the_agent_owner_team():
    registry, db = clients()
    with Stubber(registry) as stub:
        management = _approve_agent_referencing_skill(stub, registry, db)
        events = event_handler(registry, db)
        # 参照先の Skill は未管理で、ポータル外で廃止された
        stub.add_response(
            "get_registry_record", {**SKILL, "status": "DEPRECATED", "updatedAt": _now()}, SKILL_GET
        )
        result = events(registry_event("Deprecated", event_id="ev-1", record_id=SKILL_ID), None)
        assert result == {"result": "PROCESSED", "notified": 2}
        stub.assert_no_pending_responses()

        assert [item["kind"] for item in _notifications(management, ADMIN)[1]["items"]] == [
            "OUTSIDE_CHANGE"
        ]
        (item,) = _notifications(management, PUBLISHER)[1]["items"]
        assert (item["kind"], item["recordId"]) == ("REFERENCE_DEPRECATED", SKILL_ID)
        assert "決済スキル" in item["message"] and RECORD_ID in item["message"]


def _suspend_in_portal(db, record_id, team):
    """record_id を team が所有し、ポータルで公開停止した（操作記録が成功で確定した）状態を置く。"""
    started = _now().isoformat()
    put(
        db,
        "MANAGEMENT",
        {"managed": True, "ownerTeam": team, "publishedVersion": "1.2.3"},
        record_id,
    )
    principal = {"issuer": ISSUER, "subject": "carol"}
    put(
        db,
        "AUDIT#op-suspend#INTENT",
        {
            "operationId": "op-suspend",
            "kind": "SUSPEND",
            "principal": principal,
            "startedAt": started,
            "ownerTeam": team,
            "recordId": record_id,
        },
        record_id,
    )
    put(
        db,
        "AUDIT#op-suspend#RESULT",
        {"operationId": "op-suspend", "result": {}, "settledAt": _now().isoformat()},
        record_id,
    )


@mock_aws
def test_portal_suspension_of_a_reference_notifies_its_owner_and_the_agent_owner_team():
    registry, db = clients()
    with Stubber(registry) as stub:
        management = _approve_agent_referencing_skill(stub, registry, db)
        events = event_handler(registry, db)
        _suspend_in_portal(db, SKILL_ID, "team-skills")
        stub.add_response(
            "get_registry_record",
            {**SKILL, "status": "REJECTED", "updatedAt": _now(), "statusReason": "脆弱性の調査"},
            SKILL_GET,
        )
        events(registry_event("Rejected", event_id="ev-1", record_id=SKILL_ID), None)
        stub.assert_no_pending_responses()

        (owner,) = _notifications(management, "registry-publisher team-skills")[1]["items"]
        assert owner["kind"] == "SUSPENDED" and "脆弱性の調査" in owner["message"]
        (agent,) = _notifications(management, PUBLISHER)[1]["items"]
        assert agent["kind"] == "REFERENCE_SUSPENDED"
        assert _notifications(management, ADMIN)[1]["items"] == []


@mock_aws
def test_agents_whose_published_version_no_longer_uses_the_reference_are_not_notified():
    registry, db = clients()
    with Stubber(registry) as stub:
        management = _approve_agent_referencing_skill(stub, registry, db)
        events = event_handler(registry, db)
        # Agent の新しい版が、参照をやめて公開承認された
        agent = stored(db, "MANAGEMENT")
        put(db, "MANAGEMENT", {**agent, "publishedVersion": "2.0.0"})
        put(db, "PUBLISHED#2.0.0", {"relatedReferences": []})
        _suspend_in_portal(db, SKILL_ID, "team-skills")
        stub.add_response(
            "get_registry_record", {**SKILL, "status": "REJECTED", "updatedAt": _now()}, SKILL_GET
        )
        result = events(registry_event("Rejected", event_id="ev-1", record_id=SKILL_ID), None)
        assert result["notified"] == 1
        assert _notifications(management, PUBLISHER)[1]["items"] == []


@mock_aws
def test_deprecation_request_and_decline_are_notified_in_the_app():
    """廃止依頼は Curator 全員に、見送り（AWS のイベントが出ない）は所有チームの Publisher 全員に届く。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        _approve(stub, state)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        request_path = f"/api/records/{RECORD_ID}/deprecation-requests"
        status, request = call(
            state["management"],
            event("POST", request_path, {"reason": "後継の Agent に移行した"}, groups=PUBLISHER),
        )
        assert status == 200
        (item,) = _notifications(state["management"], CURATOR)[1]["items"]
        assert item["kind"] == "DEPRECATION_REQUESTED"
        assert item["recordName"] == RECORD["displayName"]
        assert "後継の Agent に移行した" in item["message"]

        # 拒否された依頼（未解決の依頼の重複）は通知を残さない
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, _ = call(
            state["management"],
            event("POST", request_path, {"reason": "もう一度依頼する"}, groups=PUBLISHER),
        )
        assert status == 409
        assert len(_notifications(state["management"], CURATOR)[1]["items"]) == 1

        status, _ = call(
            state["decision"],
            event(
                "POST",
                f"{request_path}/decline",
                {"requestId": request["requestId"], "reason": "利用者が残っている"},
                groups=CURATOR,
                sub="carol",
            ),
        )
        assert status == 200
        stub.assert_no_pending_responses()
        (declined,) = _notifications(state["management"], PUBLISHER)[1]["items"]
        assert declined["kind"] == "DEPRECATION_DECLINED"
        assert declined["recordName"] is None and RECORD_ID in declined["message"]
        assert "利用者が残っている" in declined["message"]


NINETY_DAYS = 90 * 24 * 60 * 60


def _pending_notification(stub, registry, db):
    state = setup_pending(stub, registry, db)
    _deliver(
        stub,
        event_handler(registry, db),
        "Pending Approval",
        "ev-1",
        status="PENDING_APPROVAL",
        updated_at=_now(),
    )
    return state["management"]


def _mark_read(handler, body, sub="dave", groups=CURATOR):
    return call(
        handler, event("POST", "/api/notifications/read", body, groups=groups, sub=sub, key="")
    )


@mock_aws
def test_read_position_is_kept_per_user_while_the_notification_is_shared_by_the_group():
    registry, db = clients()
    with Stubber(registry) as stub:
        management = _pending_notification(stub, registry, db)
        (item,) = _notifications(management, CURATOR, sub="dave")[1]["items"]

        status, body = _mark_read(management, {"readUpTo": item["position"]})
        assert status == 200
        assert body["unreadCount"] == 0 and body["readUpTo"] == item["position"]
        assert body["items"][0]["read"] is True
        # 同じグループの別の Curator の未読は変わらない（通知はグループで1件、既読はユーザー単位）
        other = _notifications(management, CURATOR, sub="erin")[1]
        assert other["unreadCount"] == 1 and other["items"][0]["read"] is False

        # 古い位置を送っても、既読位置は戻らない
        older = "2026-01-01T00:00:00.000000Z#" + "0" * 64
        assert _mark_read(management, {"readUpTo": older})[1]["readUpTo"] == item["position"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"readUpTo": 1},
        {"readUpTo": "latest"},
        {"readUpTo": "2026-11-14T00:00:00Z#" + "0" * 64},
        {"readUpTo": "2026-11-14T00:00:00.000000Z#" + "0" * 64, "unread": True},
    ],
)
@mock_aws
def test_read_position_body_is_validated(body):
    registry, db = clients()
    status, error = _mark_read(handler_for(registry, db), body)
    assert status == 400 and error["error"]["code"] == "VALIDATION_ERROR"


@mock_aws
def test_notifications_are_kept_for_ninety_days_and_expired_ones_are_not_shown():
    registry, db = clients()
    with Stubber(registry) as stub:
        management = _pending_notification(stub, registry, db)
        pk = f"REGISTRY#{REGISTRY_ARN}#NOTIFICATION#CURATOR"
        (item,) = db.query(
            TableName=SUPPLEMENT_TABLE,
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": {"S": pk}},
        )["Items"]
        assert abs(int(item["expiresAt"]["N"]) - (time.time() + NINETY_DAYS)) < 60
        # TTL の削除を待つ間の期限切れの通知は、一覧にも未読件数にも含めない
        db.put_item(
            TableName=SUPPLEMENT_TABLE,
            Item={
                **item,
                "sk": {"S": "2026-01-01T00:00:00.000000Z#" + "1" * 64},
                "notificationId": {"S": "1" * 64},
                "expiresAt": {"N": str(int(time.time()) - 1)},
            },
        )
        body = _notifications(management, CURATOR)[1]
        assert len(body["items"]) == 1 and body["unreadCount"] == 1


@mock_aws
def test_viewers_without_a_group_get_no_notifications():
    registry, db = clients()
    with Stubber(registry) as stub:
        management = _pending_notification(stub, registry, db)
        assert _notifications(management, "")[1] == {
            "items": [],
            "unreadCount": 0,
            "readUpTo": None,
        }
        # 所属チームのない Publisher も宛先グループを持たない
        assert _notifications(management, "registry-publisher")[1]["items"] == []


@mock_aws
def test_notifications_require_the_supplement_table_and_report_storage_failures():
    registry, db = clients()
    without_table = create_management_handler(ENV, registry_client=registry)
    status, body = _notifications(without_table, CURATOR)
    assert (status, body["error"]["code"]) == (503, "NOTIFICATIONS_NOT_CONFIGURED")
    # 補助テーブルがない（読めない）ことを0件に置き換えない
    db.delete_table(TableName=SUPPLEMENT_TABLE)
    status, body = _notifications(handler_for(registry, db), CURATOR)
    assert (status, body["error"]["code"]) == (502, "NOTIFICATIONS_UNAVAILABLE")


@mock_aws
def test_malformed_stored_notifications_are_reported_instead_of_hidden():
    registry, db = clients()
    db.put_item(
        TableName=SUPPLEMENT_TABLE,
        Item={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#NOTIFICATION#CURATOR"},
            "sk": {"S": "2026-11-14T00:00:00.000000Z#" + "2" * 64},
            "kind": {"S": "UNKNOWN"},
            "expiresAt": {"N": str(int(time.time()) + 60)},
        },
    )
    status, body = _notifications(handler_for(registry, db), CURATOR)
    assert (status, body["error"]["code"]) == (502, "NOTIFICATIONS_UNAVAILABLE")


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "aws.bedrock-agentcore"},
        {"registry_id": "otherREGISTRY"},
        {"record_id": "../agentABC1"},
    ],
)
@mock_aws
def test_events_for_other_registries_or_sources_are_ignored_without_reading_the_registry(changes):
    registry, db = clients()
    with Stubber(registry) as stub:
        result = event_handler(registry, db)(
            registry_event("Approved", event_id="ev-1", **changes), None
        )
        assert result == {"result": "IGNORED", "notified": 0}
        stub.assert_no_pending_responses()


@mock_aws
def test_registry_failures_are_raised_for_retry_and_the_event_is_processed_on_redelivery():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        events = event_handler(registry, db)
        stub.add_client_error("get_registry_record", "ThrottlingException", http_status_code=429)
        with pytest.raises(ClientError):
            events(registry_event("Pending Approval", event_id="ev-1"), None)
        # 失敗したイベントは処理済みにしないため、再配信で通知できる
        result = _deliver(
            stub, events, "Pending Approval", "ev-1", status="PENDING_APPROVAL", updated_at=_now()
        )
        assert result == {"result": "PROCESSED", "notified": 1}
        assert _notifications(state["management"], CURATOR)[1]["unreadCount"] == 1


@mock_aws
def test_events_for_deleted_records_are_marked_processed_without_notifications():
    registry, db = clients()
    with Stubber(registry) as stub:
        events = event_handler(registry, db)
        stub.add_client_error(
            "get_registry_record", "ResourceNotFoundException", http_status_code=404
        )
        assert events(registry_event("Deprecated", event_id="ev-1"), None) == {
            "result": "RECORD_NOT_FOUND",
            "notified": 0,
        }
        assert events(registry_event("Deprecated", event_id="ev-1"), None)["result"] == "DUPLICATE"
        stub.assert_no_pending_responses()


@mock_aws
def test_portal_drafts_and_in_flight_states_are_not_notified():
    registry, db = clients()
    with Stubber(registry) as stub:
        # ポータルで作成した下書き（DRAFT）は、作成の操作記録で説明できるため通知しない
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
        management = handler_for(registry, db)
        assert call(management, event(body=CUSTOM_DRAFT))[0] == 201
        events = event_handler(registry, db)
        assert _deliver(stub, events, "Draft", "ev-1", status="DRAFT", updated_at=_now()) == {
            "result": "PROCESSED",
            "notified": 0,
        }
        # 処理中の状態は遷移が確定していないため通知しない
        assert _deliver(stub, events, "Draft", "ev-2", status="UPDATING", updated_at=_now()) == {
            "result": "PROCESSED",
            "notified": 0,
        }
        stub.assert_no_pending_responses()
        assert _notifications(management, ADMIN)[1]["items"] == []


@pytest.mark.parametrize("missing", ["REGISTRY_ARN", "SUPPLEMENT_TABLE_NAME"])
def test_event_handler_does_not_start_without_registry_or_supplement_table(missing):
    environ = {"REGISTRY_ARN": REGISTRY_ARN, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE}
    del environ[missing]
    with pytest.raises(ConfigurationError):
        create_event_handler(environ)
