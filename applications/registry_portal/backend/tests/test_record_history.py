"""操作履歴 API（GET /api/records/{recordId}/history）の外部結果を検証する Seam 1 テスト。

操作記録（補助テーブルの追記記録）が上書き禁止の条件式付きで追記され、
履歴 API がロールと所有チームで権限を検査することを確認する。
"""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from catalog_helpers import ENV, ISSUER, REGISTRY_ARN, SUPPLEMENT_TABLE
from draft_helpers import RECORD_ID, call, clients, event
from record_fixtures import CUSTOM_DESCRIPTORS, CUSTOM_DRAFT, METADATA, stub_create
from decision_helpers import (
    CURATOR,
    GET,
    META,
    REASON,
    RECORD,
    decide,
    put,
    setup_pending,
    stored,
    stub_status_update,
)
from registry_portal.app import create_management_handler
from registry_portal.services.draft_store import DraftStore

ADMIN = "registry-admin"
OPERATION_ID = "0123456789abcdef0123456789abcdef"
CAROL = {"issuer": ISSUER, "subject": "carol"}


def history(
    handler, record_id=RECORD_ID, *, groups="registry-publisher team-payments", sub="alice"
):
    return call(
        handler,
        event("GET", f"/api/records/{record_id}/history", groups=groups, key="", sub=sub),
    )


def history_handler(registry, db):
    return create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        draft_store_client=db,
    )


def store(db) -> DraftStore:
    return DraftStore(SUPPLEMENT_TABLE, REGISTRY_ARN, db)


def adopt_published(db, record_id=RECORD_ID):
    """取り込み前から公開されていたレコードとして、取り込み（#16）が書く管理情報を置く。"""
    put(
        db,
        "MANAGEMENT",
        {
            **{k: v for k, v in METADATA.items() if k != "ownerTeam"},
            "managed": True,
            "ownerTeam": "team-payments",
            "publishedVersion": "1.0.0",
            "adoptedPublishedVersion": "1.0.0",
        },
        record_id,
    )


def add_adopted_version(db, version):
    """ポータル経由のレコードに、取り込み前の公開版があった状態を管理情報に書き足す。"""
    put(db, "MANAGEMENT", {**stored(db, "MANAGEMENT"), "adoptedPublishedVersion": version})


def put_intent(db, *, op_id=OPERATION_ID, sk=None):
    put(
        db,
        sk or f"AUDIT#{op_id}#INTENT",
        {
            "operationId": op_id,
            "ownerTeam": "team-payments",
            "principal": CAROL,
            "kind": "APPROVE",
            "state": "IN_PROGRESS",
            "startedAt": "2999-01-01T00:00:00+00:00",
        },
    )


class RecordingClient:
    """DynamoDB クライアントへの書き込みを記録し、Query を1件ずつのページに分けて返す。"""

    def __init__(self, client: Any, *, page_size: int | None = None) -> None:
        self._client = client
        self._page_size = page_size
        self.transactions: list[list[dict[str, Any]]] = []
        self.queries = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def transact_write_items(self, *, TransactItems):
        self.transactions.append(TransactItems)
        return self._client.transact_write_items(TransactItems=TransactItems)

    def query(self, **params):
        self.queries += 1
        if self._page_size is not None:
            params = {**params, "Limit": self._page_size}
        return self._client.query(**params)


@mock_aws
def test_history_checks_role_and_owner_team_before_reading():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # Viewer（ロールなし）と所属チームのない Publisher は読めない
        assert history(state["management"], groups="", sub="dave")[0] == 403
        assert history(state["management"], groups="registry-publisher", sub="dave")[0] == 403
        # 他チームの Publisher も読めない
        assert (
            history(state["management"], groups="registry-publisher team-cards", sub="dave")[0]
            == 403
        )
        # 所有チームの Publisher、所属チームによらず Curator と Admin は読める
        assert history(state["management"])[0] == 200
        assert history(state["management"], groups=CURATOR, sub="carol")[0] == 200
        assert history(state["management"], groups=ADMIN, sub="erin")[0] == 200
        stub.assert_no_pending_responses()


@mock_aws
def test_history_of_unknown_record_is_not_found():
    registry, db = clients()
    handler = history_handler(registry, db)
    assert history(handler)[0] == 404
    assert history(handler, "short!")[0] == 400


@mock_aws
def test_history_entries_contain_operator_hash_time_result_and_aws_request_id():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub)
        stub.add_response(
            "get_registry_record",
            {**RECORD, "status": "APPROVED", "statusReason": REASON},
            {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
        )
        status, _ = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 200

        status, body = history(state["management"])
        assert status == 200
        assert body["recordId"] == RECORD_ID
        assert body["ownerTeam"] == "team-payments"
        entries = body["entries"]
        kinds = [entry["kind"] for entry in entries]
        assert kinds == ["CREATE", "SUBMIT", "APPROVE"]
        for entry in entries:
            assert entry["operator"] is not None
            assert entry["startedAt"]
            assert entry["targetHash"]
            assert entry["operationId"]
        approve = entries[-1]
        assert approve["state"] == "SUCCEEDED"
        assert approve["awsRequestId"] == "aws-decide-1"
        assert approve["reason"] == REASON
        assert entries[0]["state"] == "SUCCEEDED"


@mock_aws
def test_operation_records_are_appended_with_no_overwrite_condition():
    """作成の操作記録が INTENT と RESULT の組でレコードの pk にも写り、上書き禁止の条件式付きで書かれる。"""
    registry, db = clients()
    recording = RecordingClient(db)
    with Stubber(registry) as stub:
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
        handler = create_management_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            draft_store_client=recording,
        )
        status, _ = call(handler, event(body=CUSTOM_DRAFT))
        assert status == 201

    # アプリが送った追記記録の書き込みは、すべて上書き禁止の条件式を持つ
    audit_puts = [
        item["Put"]
        for transaction in recording.transactions
        for item in transaction
        if "Put" in item and item["Put"]["Item"]["sk"]["S"].startswith("AUDIT#")
    ]
    record_pk = store(db).record_pk(RECORD_ID)
    assert {put["Item"]["pk"]["S"] for put in audit_puts} == {
        record_pk,
        store(db).name_lock_pk(CUSTOM_DRAFT["name"]),
    }
    assert all(put["ConditionExpression"] == "attribute_not_exists(pk)" for put in audit_puts)

    # レコード単位の写しは、正本（名前のロック側）と同じく INTENT と RESULT の組になる
    records = store(db).list_audit(RECORD_ID)
    assert sorted(record.phase for record in records) == ["INTENT", "RESULT"]
    assert {record.operation_id for record in records} == {records[0].operation_id}

    # 同じキーで追記し直すと条件式で拒否され、既存の記録は変わらない
    result = next(record for record in records if record.phase == "RESULT")
    with pytest.raises(ClientError) as conflict:
        db.put_item(
            TableName=SUPPLEMENT_TABLE,
            Item={
                **{k: TypeSerializer().serialize(v) for k, v in result.item.items()},
                "errorCode": {"S": "OVERWRITTEN"},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
    assert conflict.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    assert "errorCode" not in stored(db, result.item["sk"])


@mock_aws
def test_history_shows_unknown_decider_for_pre_existing_published_records():
    """取り込み前から公開されていたカタログ項目は、公開承認した主体を「不明」として返す。"""
    registry, db = clients()
    adopt_published(db)
    status, body = history(history_handler(registry, db))
    assert status == 200
    (entry,) = body["entries"]
    assert entry["kind"] == "APPROVE"
    assert entry["operator"] is None
    assert entry["state"] == "UNKNOWN"
    assert entry["operationId"] is None
    assert entry["recordVersion"] == "1.0.0"


@mock_aws
def test_unknown_decider_remains_after_a_later_portal_approval():
    """取り込み後にポータルで別の版を公開承認しても、取り込み前の版の「不明」は履歴から消えない。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        add_adopted_version(db, "0.9.0")
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub)
        stub.add_response(
            "get_registry_record",
            {**RECORD, "status": "APPROVED", "statusReason": REASON},
            {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
        )
        status, _ = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 200

        status, body = history(state["management"])
    assert status == 200
    states = [(entry["kind"], entry["state"]) for entry in body["entries"]]
    assert states[0] == ("APPROVE", "UNKNOWN")
    assert body["entries"][0]["recordVersion"] == "0.9.0"
    assert ("APPROVE", "SUCCEEDED") in states


@mock_aws
def test_history_of_unsettled_operation_keeps_state_open():
    """結果が確定していない操作（INTENT だけの追記記録）は、結果確認中として返す。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        put_intent(db)
        status, body = history(state["management"])
    assert status == 200
    last = body["entries"][-1]
    assert last["kind"] == "APPROVE"
    assert last["state"] == "IN_PROGRESS"
    assert last["operator"] == CAROL


@mock_aws
def test_history_reads_every_query_page():
    """Query の応答が複数ページに分かれても、すべての操作記録を履歴に含める。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        setup_pending(stub, registry, db)
    paged = RecordingClient(db, page_size=1)
    status, body = history(history_handler(registry, paged))
    assert status == 200
    assert [entry["kind"] for entry in body["entries"]] == ["CREATE", "SUBMIT"]
    assert paged.queries > 1


@mock_aws
def test_malformed_operation_record_is_reported_instead_of_hidden():
    """ソートキーの形式が崩れた操作記録は捨てずに、保存内容の異常として返す。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        put_intent(db, sk="AUDIT#broken")
        status, body = history(state["management"])
    assert status == 502
    assert body["error"]["code"] == "HISTORY_UNAVAILABLE"
    assert "形式が正しくありません" in body["error"]["message"]


@mock_aws
def test_history_without_supplement_table_reports_not_configured():
    """補助テーブルが未設定なら、500 ではなく未設定のエラーを返す。"""
    registry, _ = clients()
    handler = create_management_handler({**ENV}, registry_client=registry)
    status, body = history(handler, groups=CURATOR, sub="carol")
    assert status == 503
    assert body["error"]["code"] == "DRAFTS_NOT_CONFIGURED"
