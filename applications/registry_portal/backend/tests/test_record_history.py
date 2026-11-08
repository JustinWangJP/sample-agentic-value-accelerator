"""操作履歴 API（GET /api/records/{recordId}/history）の外部結果を検証する Seam 1 テスト。

操作記録（補助テーブルの追記記録）が上書き禁止の条件式付きで追記され、
履歴 API がロールと所有チームで権限を検査することを確認する。
"""

from __future__ import annotations


from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
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
    setup_pending,
    stub_status_update,
)
from registry_portal.app import create_management_handler

CURATOR_GROUPS = "registry-curator team-cards"


def history(
    handler, record_id=RECORD_ID, *, groups="registry-publisher team-payments", sub="alice"
):
    return call(
        handler,
        event("GET", f"/api/records/{record_id}/history", groups=groups, key="", sub=sub),
    )


def audit_items(db, record_id=RECORD_ID):
    """レコードの pk に追記された操作記録（AUDIT 項目）をすべて返す。"""
    pk = f"REGISTRY#{REGISTRY_ARN}#RECORD#{record_id}"
    response = db.query(
        TableName=SUPPLEMENT_TABLE,
        KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
        ExpressionAttributeValues={":pk": {"S": pk}, ":sk": {"S": "AUDIT#"}},
    )
    return [
        {k: TypeDeserializer().deserialize(v) for k, v in item.items()}
        for item in response["Items"]
    ]


def adopt_published(db, record_id=RECORD_ID):
    """取り込み前から公開されていたレコードとして、公開版だけの管理情報を置く。"""
    pk = f"REGISTRY#{REGISTRY_ARN}#RECORD#{record_id}"
    db.put_item(
        TableName=SUPPLEMENT_TABLE,
        Item={
            "pk": {"S": pk},
            "sk": {"S": "MANAGEMENT"},
            "managed": {"BOOL": True},
            "ownerTeam": {"S": "team-payments"},
            "publishedVersion": {"S": "1.0.0"},
            **{k: TypeSerializer().serialize(v) for k, v in METADATA.items() if k != "ownerTeam"},
        },
    )


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
        # 所有チームの Publisher と Curator は読める
        assert history(state["management"])[0] == 200
        assert history(state["management"], groups=CURATOR, sub="carol")[0] == 200
        stub.assert_no_pending_responses()


@mock_aws
def test_history_of_unknown_record_is_not_found():
    registry, db = clients()
    handler = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        draft_store_client=db,
    )
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
    """作成の操作記録がレコードの pk にも追記され、上書き禁止の条件式付きで保存される。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
        handler = create_management_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            draft_store_client=db,
        )
        status, _ = call(handler, event(body=CUSTOM_DRAFT))
        assert status == 201
        pk = f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"
        record_audits = [item for item in audit_items(db) if item["pk"] == pk]
        assert [item["sk"].split("#")[2] for item in record_audits] == ["RESULT"]
        with pytest.raises(Exception) as conflict:
            db.put_item(
                TableName=SUPPLEMENT_TABLE,
                Item={
                    "pk": {"S": pk},
                    "sk": {"S": record_audits[0]["sk"]},
                    "operationId": {"S": "x"},
                    **{
                        k: TypeSerializer().serialize(v)
                        for k, v in record_audits[0].items()
                        if k not in ("pk", "sk")
                    },
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        assert "ConditionalCheckFailed" in str(conflict.value)
        # 同じ操作 ID で追記し直しても、既存の記録は変わらない
        assert len(audit_items(db)) == len([i for i in audit_items(db) if i["pk"] == pk])


@mock_aws
def test_history_shows_unknown_approver_for_pre_existing_published_records():
    """取り込み前から公開されていたカタログ項目は、承認者を「不明」として返す。"""
    registry, db = clients()
    adopt_published(db)
    handler = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        draft_store_client=db,
    )
    status, body = history(handler)
    assert status == 200
    (entry,) = body["entries"]
    assert entry["kind"] == "APPROVE"
    assert entry["operator"] is None
    assert entry["state"] == "UNKNOWN"
    assert entry["operationId"] is None


@mock_aws
def test_history_of_in_progress_operation_keeps_state_open():
    """結果が確定していない操作（INTENT だけの追記記録）は、進行中として表示する。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        pk = f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"
        db.put_item(
            TableName=SUPPLEMENT_TABLE,
            Item={
                "pk": {"S": pk},
                "sk": {"S": "AUDIT#0123456789abcdef0123456789abcdef#INTENT"},
                "operationId": {"S": "0123456789abcdef0123456789abcdef"},
                "ownerTeam": {"S": "team-payments"},
                "principal": {"M": {"issuer": {"S": ISSUER}, "subject": {"S": "carol"}}},
                "kind": {"S": "APPROVE"},
                "state": {"S": "IN_PROGRESS"},
                "lockPk": {"S": pk},
                "startedAt": {"S": "2999-01-01T00:00:00+00:00"},
            },
        )
        status, body = history(state["management"])
        assert status == 200
        last = body["entries"][-1]
        assert last["kind"] == "APPROVE"
        assert last["state"] == "IN_PROGRESS"
        assert last["operator"] == {"issuer": ISSUER, "subject": "carol"}
