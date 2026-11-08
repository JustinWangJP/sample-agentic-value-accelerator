"""審査と決裁 API の外部結果を検証する Seam 1 テスト。"""

import json
from collections import deque
from datetime import datetime, timezone

from boto3.dynamodb.types import TypeSerializer
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
from draft_helpers import RECORD_ARN, RECORD_ID, aws_record, call, clients, event
from record_fixtures import CUSTOM_DESCRIPTORS, CUSTOM_DRAFT, METADATA, stub_create
from registry_portal.app import create_curation_handler, create_management_handler
from registry_portal.services.discovery import DiscoveryDependencies

GET = {"registryId": REGISTRY_ARN, "recordId": RECORD_ID}
META = {**METADATA, "isA2A": False}
RECORD = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
CURATOR = "registry-curator"
REASON = "利用申請の内容を確認した"


def stored(db, sk, record_id=RECORD_ID):
    from boto3.dynamodb.types import TypeDeserializer

    item = db.get_item(
        TableName=SUPPLEMENT_TABLE,
        Key={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{record_id}"},
            "sk": {"S": sk},
        },
    ).get("Item")
    return None if item is None else {k: TypeDeserializer().deserialize(v) for k, v in item.items()}


def put(db, sk, item, record_id=RECORD_ID):
    db.put_item(
        TableName=SUPPLEMENT_TABLE,
        Item={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{record_id}"},
            "sk": {"S": sk},
            **{k: TypeSerializer().serialize(v) for k, v in item.items()},
        },
    )


def missing_published(record_id=RECORD_ID):
    """発見 API が公開中の版を見つけられなかった応答。"""
    return json_response(
        200,
        {
            "registryRecords": [],
            "errors": [{"recordId": record_id, "errorCode": "RESOURCE_NOT_FOUND"}],
        },
    )


def stub_submit(stub, record=RECORD):
    """下書きを審査待ちにする申請の一連の応答を積む。"""
    stub.add_response("get_registry_record", record, GET)
    stub.add_response(
        "get_registry",
        {
            "name": "registry",
            "registryId": "abcdEFGH1234",
            "registryArn": REGISTRY_ARN,
            "status": "ACTIVE",
            "createdAt": record["createdAt"],
            "updatedAt": record["updatedAt"],
            "approvalConfiguration": {"autoApprovalRules": []},
        },
        {"registryId": REGISTRY_ARN},
    )
    stub.add_response(
        "submit_registry_record_for_approval",
        {
            "registryArn": REGISTRY_ARN,
            "recordArn": RECORD_ARN,
            "recordId": RECORD_ID,
            "status": "PENDING_APPROVAL",
            "updatedAt": record["updatedAt"],
        },
        GET,
    )
    stub.add_response("get_registry_record", {**record, "status": "PENDING_APPROVAL"}, GET)


def setup_pending(stub, registry, db, *, editor="alice", submitter="bob"):
    """下書きを作成して申請し、審査待ちの状態を作る。管理 handler と決裁 handler を返す。"""
    transport = FakeTransport(registry=deque([missing_published()]))
    management = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        draft_store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
    )
    decision = create_curation_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        store_client=db,
    )
    stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
    _, created = call(management, event(body=CUSTOM_DRAFT, sub=editor))
    stub_submit(stub)
    status, submitted = call(
        management,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/submit",
            {
                "expectedContentHash": created["contentHash"],
                "reason": "利用者向けに公開する",
                "relatedReferences": [],
            },
            sub=submitter,
            key="submit-1",
        ),
    )
    assert status == 200
    snapshot = stored(db, f'SUBMISSION#{submitted["submissionId"]}')
    return {
        "management": management,
        "decision": decision,
        "submitted": submitted,
        "snapshot": snapshot,
    }


def decide(
    handler,
    approved=True,
    *,
    snapshot_hash,
    reason=REASON,
    sub="carol",
    groups=CURATOR,
    key="decide-1",
):
    return call(
        handler,
        event(
            "POST",
            f"/api/reviews/{RECORD_ID}/{'approve' if approved else 'reject'}",
            {"snapshotHash": snapshot_hash, "reason": reason},
            groups=groups,
            sub=sub,
            key=key,
        ),
    )


def stub_status_update(stub, approved=True, reason=REASON, record=RECORD, error=None):
    target = "APPROVED" if approved else "REJECTED"
    params = {**GET, "status": target, "statusReason": reason}
    if error:
        stub.add_client_error("update_registry_record_status", error, expected_params=params)
    else:
        stub.add_response(
            "update_registry_record_status",
            {
                "registryArn": REGISTRY_ARN,
                "recordArn": RECORD_ARN,
                "recordId": RECORD_ID,
                "status": target,
                "statusReason": reason,
                "updatedAt": record["updatedAt"],
                "ResponseMetadata": {"RequestId": "aws-decide-1"},
            },
            params,
        )


def list_response(*records, next_token=None):
    body = {"registryRecords": list(records)}
    if next_token is not None:
        body["nextToken"] = next_token
    return body


def aws_summary(status="PENDING_APPROVAL", record_id=RECORD_ID, **changes):
    """ListRegistryRecords が返す RegistryRecordSummary。"""
    record = {
        "registryArn": REGISTRY_ARN,
        "recordArn": f"{REGISTRY_ARN}/record/{record_id}",
        "recordId": record_id,
        "name": CUSTOM_DRAFT["name"],
        "displayName": CUSTOM_DRAFT["displayName"],
        "description": CUSTOM_DRAFT["description"],
        "recordType": "AGENT",
        "recordVersion": CUSTOM_DRAFT["recordVersion"],
        "status": status,
        "createdAt": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "updatedAt": datetime(2026, 11, 2, tzinfo=timezone.utc),
    }
    record.update(changes)
    return record


LIST_PARAMS = {
    "registryId": REGISTRY_ARN,
    "maxResults": 20,
    "filters": [{"name": "status", "values": ["PENDING_APPROVAL"]}],
}


@mock_aws
def test_reviews_require_the_curator_role_before_any_aws_call():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        for groups in ("", "registry-publisher team-payments", "registry-admin"):
            request = event("GET", "/api/reviews", groups=groups, key="")
            assert call(state["decision"], request)[0] == 403
            request = event(
                "POST",
                f"/api/records/../reviews/{RECORD_ID}/approve",
                groups=groups,
                key="decide-1",
            )
            assert call(state["decision"], request)[0] == 404
        stub.assert_no_pending_responses()


@mock_aws
def test_review_list_joins_aws_pending_records_with_submission_snapshots():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("list_registry_records", list_response(aws_summary()), LIST_PARAMS)
        stub.add_response(
            "list_registry_records",
            list_response(),
            {**LIST_PARAMS, "nextToken": "next"},
        )
        status, body = call(state["decision"], event("GET", "/api/reviews", groups=CURATOR, key=""))
        assert status == 200
        assert body["nextToken"] is None
        (item,) = body["items"]
        assert item["recordId"] == RECORD_ID
        assert item["status"] == "PENDING_APPROVAL"
        assert item["ownerTeam"] == "team-payments"
        assert item["recordVersion"] == CUSTOM_DRAFT["recordVersion"]
        assert item["submissionId"] == state["submitted"]["submissionId"]
        assert item["snapshotHash"] == state["snapshot"]["snapshotHash"]
        assert item["reason"] == "利用者向けに公開する"
        assert item["submittedBy"] == {"issuer": ISSUER, "subject": "bob"}
        assert item["submittedAt"] == state["snapshot"]["submittedAt"]
        assert item["managed"] is True
        assert item["invalidated"] is False
        assert item["operationId"] is None
        status, second = call(
            state["decision"],
            event("GET", "/api/reviews", groups=CURATOR, key="")
            | {"queryStringParameters": {"nextToken": "next"}},
        )
        assert second["nextToken"] is None
        stub.assert_no_pending_responses()


@mock_aws
def test_review_list_shows_pending_records_without_a_portal_submission():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response(
            "list_registry_records",
            list_response(aws_summary(), aws_summary(record_id="otherABC1234")),
            LIST_PARAMS,
        )
        status, body = call(state["decision"], event("GET", "/api/reviews", groups=CURATOR, key=""))
        assert status == 200
        unmanaged = body["items"][1]
        assert unmanaged["managed"] is False
        assert unmanaged["ownerTeam"] is None
        assert unmanaged["submissionId"] is None
        assert unmanaged["reason"] is None
        stub.assert_no_pending_responses()


@mock_aws
def test_review_detail_returns_the_snapshot_and_the_published_diff_base():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = call(
            state["decision"],
            event("GET", f"/api/reviews/{RECORD_ID}", groups=CURATOR, key=""),
        )
        assert status == 200
        assert body["status"] == "PENDING_APPROVAL"
        assert body["snapshotHash"] == state["snapshot"]["snapshotHash"]
        assert body["snapshot"]["reason"] == "利用者向けに公開する"
        assert body["snapshot"]["record"]["descriptors"] == CUSTOM_DESCRIPTORS
        assert body["snapshot"]["contact"] == CUSTOM_DRAFT["contact"]
        assert body["published"] is None
        stub.assert_no_pending_responses()


@mock_aws
def test_review_detail_includes_the_published_snapshot_for_the_diff():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 既に公開済みの版がある審査（差分の比較対象）
        put(
            db,
            "PUBLISHED#0.9.0",
            {
                "contact": "old@example.com",
                "termsOfUse": "旧利用条件",
                "connectionReference": "https://example.com/old",
                "relatedReferences": [],
            },
        )
        management = stored(db, "MANAGEMENT")
        put(db, "MANAGEMENT", {**management, "publishedVersion": "0.9.0"})
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = call(
            state["decision"],
            event("GET", f"/api/reviews/{RECORD_ID}", groups=CURATOR, key=""),
        )
        assert status == 200
        assert body["published"]["recordVersion"] == "0.9.0"
        assert body["published"]["contact"] == "old@example.com"
        assert body["published"]["termsOfUse"] == "旧利用条件"
        assert body["published"]["relatedReferences"] == []
        stub.assert_no_pending_responses()


@mock_aws
def test_review_detail_hides_decided_records():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, body = call(
            state["decision"],
            event("GET", f"/api/reviews/{RECORD_ID}", groups=CURATOR, key=""),
        )
        assert status == 404
        assert body["error"]["code"] == "REVIEW_NOT_FOUND"
        stub.assert_no_pending_responses()


@mock_aws
def test_approve_records_intent_then_calls_aws_and_writes_the_published_snapshot():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        intents = []

        def check_intent(params, **kwargs):
            lock = stored(db, "LOCK")
            assert lock is not None, "AWS の呼び出し前に決裁の意図を記録する"
            intents.append(stored(db, f"AUDIT#{lock['operationId']}#INTENT"))

        registry.meta.events.register(
            "before-parameter-build.agent-registry-control.UpdateRegistryRecordStatus",
            check_intent,
        )
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
        assert body["status"] == "APPROVED"
        assert body["publishedVersion"] == CUSTOM_DRAFT["recordVersion"]
        assert body["submissionId"] == state["submitted"]["submissionId"]
        assert intents and intents[0]["reason"] == REASON
        assert intents[0]["kind"] == "APPROVE"
        # Curator が確認した申請のスナップショットを公開版として保存する
        published = stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}")
        assert published["contact"] == CUSTOM_DRAFT["contact"]
        assert published["termsOfUse"] == CUSTOM_DRAFT["termsOfUse"]
        assert published["connectionReference"] == CUSTOM_DRAFT["connectionReference"]
        assert published["relatedReferences"] == []
        management = stored(db, "MANAGEMENT")
        assert management["publishedVersion"] == CUSTOM_DRAFT["recordVersion"]
        assert management["activeSubmissionId"] is None
        assert management["ownerTeam"] == "team-payments"
        assert stored(db, "LOCK") is None
        result = stored(db, f'AUDIT#{body["operationId"]}#RESULT')
        assert result["awsRequestId"] == "aws-decide-1"
        stub.assert_no_pending_responses()


@mock_aws
def test_approve_writes_related_references_from_the_submission_snapshot():
    registry, db = clients()
    with Stubber(registry) as stub:
        transport = FakeTransport(
            registry=deque(
                [
                    missing_published(),
                    json_response(
                        200,
                        {
                            "registryRecords": [
                                {
                                    **summary("skillABC1234", "SKILL", recordVersion="1.2.3"),
                                    "descriptors": CUSTOM_DESCRIPTORS,
                                }
                            ],
                            "errors": [],
                        },
                    ),
                ]
            )
        )
        management = create_management_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            draft_store_client=db,
            discovery=DiscoveryDependencies(
                transport=transport, secrets_client=FakeSecrets().client
            ),
        )
        decision = create_curation_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            store_client=db,
        )
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
        _, created = call(management, event(body=CUSTOM_DRAFT))
        stub_submit(stub)
        status, submitted = call(
            management,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": created["contentHash"],
                    "reason": "参照付きで公開する",
                    "relatedReferences": [{"recordId": "skillABC1234", "recordVersion": "1.2.3"}],
                },
                sub="bob",
                key="submit-1",
            ),
        )
        assert status == 200
        snapshot = stored(db, f'SUBMISSION#{submitted["submissionId"]}')
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, reason=REASON)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, _ = decide(decision, snapshot_hash=snapshot["snapshotHash"])
        assert status == 200
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}")["relatedReferences"] == [
            {"recordId": "skillABC1234", "recordVersion": "1.2.3"}
        ]
        stub.assert_no_pending_responses()


@mock_aws
def test_approve_rejects_a_stale_snapshot_hash_with_409_without_calling_aws():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = decide(state["decision"], snapshot_hash="0" * 64)
        assert status == 409
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_approve_refuses_a_record_changed_after_the_submission():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        changed = aws_record(
            CUSTOM_DRAFT,
            descriptors=CUSTOM_DESCRIPTORS,
            customMetadata=META,
            description="審査中に書き換えられた",
        )
        stub.add_response("get_registry_record", {**changed, "status": "PENDING_APPROVAL"}, GET)
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 409
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_applicant_and_last_editor_cannot_decide_even_as_admin():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db, editor="alice", submitter="bob")
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        # 申請者（bob）は決裁できない
        status, body = decide(
            state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"], sub="bob"
        )
        assert status == 403
        assert "申請者と最終編集者" in body["error"]["message"]
        # 最終編集者（alice）も決裁できない。Admin を兼ねていても同じ
        status, _ = decide(
            state["decision"],
            snapshot_hash=state["snapshot"]["snapshotHash"],
            sub="alice",
            groups="registry-curator registry-admin",
        )
        assert status == 403
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("status", ["REJECTED", "APPROVED", "DEPRECATED", "DRAFT"])
@pytest.mark.parametrize("approved", [True, False])
@mock_aws
def test_decisions_refuse_records_not_awaiting_review(status, approved):
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": status}, GET)
        status_code, body = decide(
            state["decision"], approved=approved, snapshot_hash=state["snapshot"]["snapshotHash"]
        )
        assert status_code == 409
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("status", ["CREATING", "UPDATING", "CREATE_FAILED", "UPDATE_FAILED"])
@mock_aws
def test_decisions_refuse_records_aws_is_still_processing(status):
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": status}, GET)
        status_code, body = decide(
            state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"]
        )
        assert status_code == 409
        assert "処理中" in body["error"]["message"]
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_decisions_refuse_invalidated_submissions_and_records_without_one():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 承認待ちの間に編集されて無効になった申請
        put(
            db,
            f"SUBMISSION#{state['submitted']['submissionId']}#INVALIDATED",
            {"operationId": "edit-op", "invalidatedAt": "2026-11-02T00:00:00+00:00"},
        )
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 409
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()
    db.delete_table(TableName=SUPPLEMENT_TABLE)

    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        management = stored(db, "MANAGEMENT")
        put(db, "MANAGEMENT", {**management, "activeSubmissionId": None})
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 409
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("approved", "reason", "message_part"),
    [
        (False, "", "却下理由"),
        (False, " ", "却下理由"),
        (True, "x" * 256, "255"),
        (False, "x" * 256, "255"),
    ],
)
@mock_aws
def test_decision_reason_validation(approved, reason, message_part):
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        status, body = decide(
            state["decision"],
            approved=approved,
            snapshot_hash=state["snapshot"]["snapshotHash"],
            reason=reason,
        )
        assert status == 400
        assert message_part in body["error"]["message"]
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_decision_never_accepts_an_aws_status_or_unknown_fields():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        request = event(
            "POST",
            f"/api/reviews/{RECORD_ID}/approve",
            {
                "snapshotHash": state["snapshot"]["snapshotHash"],
                "reason": REASON,
                "status": "APPROVED",
            },
            groups=CURATOR,
        )
        assert call(state["decision"], request)[0] == 400
        request["body"] = json.dumps({"reason": REASON})
        assert call(state["decision"], request)[0] == 400
        request["body"] = json.dumps(
            {"snapshotHash": state["snapshot"]["snapshotHash"], "reason": REASON}
        )
        request["headers"] = {}
        assert call(state["decision"], request)[0] == 400
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_decision_requires_an_idempotency_key_like_the_submission():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        request = event(
            "POST",
            f"/api/reviews/{RECORD_ID}/approve",
            {"snapshotHash": state["snapshot"]["snapshotHash"], "reason": REASON},
            groups=CURATOR,
            key="",
        )
        assert call(state["decision"], request)[0] == 400
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_reject_records_the_reason_and_clears_the_active_submission():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=False, reason="形式が不備のため")
        stub.add_response("get_registry_record", {**RECORD, "status": "REJECTED"}, GET)
        status, body = decide(
            state["decision"],
            approved=False,
            snapshot_hash=state["snapshot"]["snapshotHash"],
            reason="形式が不備のため",
        )
        assert status == 200
        assert body["status"] == "REJECTED"
        assert body["operationState"] == "SUCCEEDED"
        assert body["publishedVersion"] is None
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}") is None
        assert stored(db, "MANAGEMENT")["activeSubmissionId"] is None
        assert stored(db, "MANAGEMENT").get("publishedVersion") is None
        assert stored(db, f'AUDIT#{body["operationId"]}#INTENT')["reason"] == "形式が不備のため"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_aws_rejection_fails_the_decision_and_releases_the_lock():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, error="ValidationException")
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 400
        assert stored(db, "LOCK") is None
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}") is None
        stub.assert_no_pending_responses()
    db.delete_table(TableName=SUPPLEMENT_TABLE)

    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, error="ThrottlingException")
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 429
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_aws_conflict_fails_the_decision_as_a_settled_409():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, error="ConflictException")
        status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 409
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_unknown_aws_outcome_stays_verifying_and_the_same_key_settles_it():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, error="InternalServerException")
        status, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 202
        assert pending["operationState"] == "VERIFYING"
        operation_id = pending["operationId"]
        assert stored(db, "LOCK")["operationId"] == operation_id
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}") is None
        # 同じキー・同じ内容の再送は AWS を呼び直さず、現物から確定する
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, finished = decide(
            state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"]
        )
        assert status == 200
        assert finished["operationState"] == "SUCCEEDED"
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}")["contact"] == (
            CUSTOM_DRAFT["contact"]
        )
        assert stored(db, "LOCK") is None
        # 同じキーに異なる内容は指定できない
        status, _ = decide(
            state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"], reason="別の理由"
        )
        assert status == 409
        stub.assert_no_pending_responses()


@mock_aws
def test_reflection_delay_stays_verifying_until_the_operations_endpoint_settles():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 202
        operation_id = pending["operationId"]
        assert stored(db, "LOCK")["operationId"] == operation_id
        # 管理 handler の操作照会は AWS を呼び直さず、観測できる状態から確定する
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, finished = call(
            state["management"],
            event("GET", f"/api/operations/{operation_id}", groups=CURATOR, key="", sub="carol"),
        )
        assert status == 200
        assert finished["operationState"] == "SUCCEEDED"
        assert finished["status"] == "APPROVED"
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}")["contact"] == (
            CUSTOM_DRAFT["contact"]
        )
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_operations_endpoint_serves_decision_ops_to_curators_only():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, error="InternalServerException")
        _, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        operation_id = pending["operationId"]
        for groups in ("", "registry-publisher team-payments", "registry-admin"):
            request = event(
                "GET", f"/api/operations/{operation_id}", groups=groups, key="", sub="carol"
            )
            assert call(state["management"], request)[0] == 403
        stub.add_response("get_registry_record", {**RECORD, "status": "UPDATING"}, GET)
        status, verifying = call(
            state["management"],
            event("GET", f"/api/operations/{operation_id}", groups=CURATOR, key="", sub="dave"),
        )
        assert status == 202
        assert verifying["operationState"] == "VERIFYING"
        assert stored(db, "LOCK")["operationId"] == operation_id
        stub.assert_no_pending_responses()


@mock_aws
def test_store_failure_after_aws_success_keeps_the_decision_verifying():
    from botocore.exceptions import ClientError

    registry, db = clients()
    fail_once = [True]

    def fail_completion(params, **kwargs):
        if fail_once[0] and any(
            item.get("Put", {}).get("Item", {}).get("state") == {"S": "SUCCEEDED"}
            and item["Put"]["Item"].get("kind") == {"S": "APPROVE"}
            for item in params["TransactItems"]
        ):
            fail_once[0] = False
            raise ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "補助ストア障害"}},
                "TransactWriteItems",
            )

    db.meta.events.register("before-parameter-build.dynamodb.TransactWriteItems", fail_completion)
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert status == 202
        assert pending["operationState"] == "VERIFYING"
        assert stored(db, "LOCK")["operationId"] == pending["operationId"]
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, finished = decide(
            state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"]
        )
        assert status == 200
        assert finished["operationState"] == "SUCCEEDED"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_curation_handler_requires_the_supplement_table_configuration():
    registry, db = clients()
    with pytest.raises(Exception, match="SUPPLEMENT_TABLE_NAME"):
        create_curation_handler(dict(ENV), registry_client=registry, store_client=db)
