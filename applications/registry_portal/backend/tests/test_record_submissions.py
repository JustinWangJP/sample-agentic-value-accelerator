"""申請 API の外部結果と固定内容を検証する Seam 1 テスト。"""

import hashlib
import json
from collections import deque

from boto3.dynamodb.types import TypeDeserializer
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
from record_fixtures import CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, METADATA, stub_create
from registry_portal.app import create_management_handler
from registry_portal.domain.drafts import RecordDraft
from registry_portal.services.draft_store import DraftStore
from registry_portal.services.discovery import DiscoveryDependencies

GET = {"registryId": REGISTRY_ARN, "recordId": RECORD_ID}
META = {**METADATA, "isA2A": False}


def stored(db, sk):
    item = db.get_item(
        TableName=SUPPLEMENT_TABLE,
        Key={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"},
            "sk": {"S": sk},
        },
    ).get("Item")
    return None if item is None else {k: TypeDeserializer().deserialize(v) for k, v in item.items()}


def absent(record_id=RECORD_ID):
    return json_response(
        200,
        {
            "registryRecords": [],
            "errors": [
                {"recordId": record_id, "errorCode": "RESOURCE_NOT_FOUND"},
            ],
        },
    )


def setup_draft(stub, registry, db, responses=None):
    transport = FakeTransport(registry=deque(responses or [absent()]))
    secrets = FakeSecrets("secret")
    handler = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        draft_store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=secrets.client),
    )
    stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
    status, created = call(handler, event(body=CUSTOM_DRAFT))
    assert status == 201
    return handler, created, transport


def submit(handler, created, *, sub="alice", key="draft-1", **changes):
    return call(
        handler,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/submit",
            {
                "expectedContentHash": created["contentHash"],
                "reason": "利用者向けに公開する",
                "relatedReferences": [],
                **changes,
            },
            sub=sub,
            key=key,
        ),
    )


@pytest.mark.parametrize("with_reference", [False, True])
@mock_aws
def test_submit_fixes_the_actual_record_and_actor_before_aws_submission(with_reference):
    registry, db = clients()
    with Stubber(registry) as stub:
        references = (
            [{"recordId": "skillABC1234", "recordVersion": "1.2.3"}] if with_reference else []
        )
        responses = (
            [absent(), published("1.2.3", "skillABC1234", "SKILL")]
            if with_reference
            else [absent()]
        )
        handler, created, _ = setup_draft(stub, registry, db, responses)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
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
        seen = []

        def check_saved_snapshot(params, **kwargs):
            lock = stored(db, "LOCK")
            saved = stored(db, f"SUBMISSION#{lock['operationId']}")
            assert saved["reason"] == "利用者向けに公開する"
            assert "expiresAt" not in saved
            assert stored(db, "MANAGEMENT").get("activeSubmissionId") is None
            seen.append(True)

        registry.meta.events.register(
            "before-parameter-build.agent-registry-control.SubmitRegistryRecordForApproval",
            check_saved_snapshot,
        )
        status, body = submit(handler, created, sub="bob", relatedReferences=references)
        assert seen == [True]
        assert status == 200
        assert body["status"] == "PENDING_APPROVAL"
        snapshot = stored(db, f'SUBMISSION#{body["submissionId"]}')
        assert snapshot["submittedBy"] == {"issuer": ISSUER, "subject": "bob"}
        assert snapshot["lastEditedBy"] == {"issuer": ISSUER, "subject": "alice"}
        assert snapshot["reason"] == "利用者向けに公開する"
        assert snapshot["record"]["descriptors"] == CUSTOM_DESCRIPTORS
        assert snapshot["record"]["customMetadata"] == META
        assert snapshot["relatedReferences"] == references
        assert snapshot["contact"] == CUSTOM_DRAFT["contact"]
        assert snapshot["termsOfUse"] == CUSTOM_DRAFT["termsOfUse"]
        assert snapshot["connectionReference"] == CUSTOM_DRAFT["connectionReference"]
        unsigned = {k: v for k, v in snapshot.items() if k not in {"pk", "sk", "snapshotHash"}}
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert body["snapshotHash"] == hashlib.sha256(canonical.encode()).hexdigest()
        assert stored(db, "MANAGEMENT")["activeSubmissionId"] == body["submissionId"]
        stub.assert_no_pending_responses()


@mock_aws
def test_submission_rejects_outside_team_and_publishers_without_teams_before_aws():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, transport = setup_draft(stub, registry, db)
        for groups in (
            "registry-publisher team-other",
            "registry-publisher",
            "registry-curator team-payments",
        ):
            request = event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": created["contentHash"],
                    "reason": "公開する",
                    "relatedReferences": [],
                },
                groups=groups,
            )
            status, body = call(handler, request)
            assert status == 403
            assert body["error"]["code"] == "FORBIDDEN"
        assert transport.requests == []
        assert stored(db, "LOCK") is None


@mock_aws
def test_submission_refuses_automatic_approval_without_an_aws_write():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
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
                "approvalConfiguration": {"autoApprovalRules": ["APPROVE_ALL"]},
            },
            {"registryId": REGISTRY_ARN},
        )
        status, body = submit(handler, created)
        assert status == 503
        assert body["error"]["code"] == "APPROVAL_CONFIGURATION_CHANGED"
        assert stored(db, "LOCK") is None
        assert stored(db, "MANAGEMENT").get("activeSubmissionId") is None
        stub.assert_no_pending_responses()


def published(version, record_id=RECORD_ID, kind="AGENT"):
    return json_response(
        200,
        {
            "registryRecords": [
                {
                    **summary(record_id, kind, recordVersion=version),
                    "descriptors": CUSTOM_DESCRIPTORS,
                }
            ],
            "errors": [],
        },
    )


@pytest.mark.parametrize(
    ("published_version", "draft_version", "accepted"),
    [
        ("1.0.0", "1.0.0", False),
        ("2.0.0", "1.9.9", False),
        ("1.0.0", "1.0.1", True),
        ("1.0.0-alpha.2", "1.0.0-alpha.10", True),
        ("1.0.0-rc.1", "1.0.0", True),
        ("1.0.0", "1.0.1-rc.1", True),
    ],
)
@mock_aws
def test_submission_compares_semver_with_the_actual_published_version(
    published_version, draft_version, accepted
):
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db, [published(published_version)])
        record = aws_record(
            {**CUSTOM_DRAFT, "recordVersion": draft_version},
            descriptors=CUSTOM_DESCRIPTORS,
            customMetadata=META,
        )
        stub.add_response("get_registry_record", record, GET)
        stub.add_response("get_registry_record", record, GET)
        status, current = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        if accepted:
            # バージョン検証を通った場合だけ設定確認まで到達する。
            stub.add_client_error(
                "get_registry",
                "AccessDeniedException",
                expected_params={"registryId": REGISTRY_ARN},
            )
        status, body = submit(handler, current)
        assert status == (502 if accepted else 400)
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_submission_rejects_a_reference_to_an_unpublished_or_different_version():
    for target in (absent("skillABC1234"), published("2.0.0", "skillABC1234", "SKILL")):
        registry, db = clients()
        with Stubber(registry) as stub:
            handler, created, _ = setup_draft(stub, registry, db, [absent(), target])
            stub.add_response(
                "get_registry_record",
                aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META),
                GET,
            )
            status, body = submit(
                handler,
                created,
                relatedReferences=[{"recordId": "skillABC1234", "recordVersion": "1.0.0"}],
            )
            assert status == 400
            assert body["error"]["code"] == "VALIDATION_ERROR"
            assert stored(db, "LOCK") is None
            stub.assert_no_pending_responses()
        db.delete_table(TableName=SUPPLEMENT_TABLE)


@mock_aws
def test_skill_submission_retains_provenance_supplement_for_review():
    from record_fixtures import SKILL_DRAFT, SKILL_DESCRIPTORS

    registry, db = clients()
    with Stubber(registry) as stub:
        transport = FakeTransport(registry=deque([absent()]))
        secrets = FakeSecrets("secret")
        handler = create_management_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            draft_store_client=db,
            discovery=DiscoveryDependencies(transport=transport, secrets_client=secrets.client),
        )
        stub_create(stub, SKILL_DRAFT, SKILL_DESCRIPTORS, METADATA)
        _, created = call(handler, event(body=SKILL_DRAFT))
        record = aws_record(SKILL_DRAFT, descriptors=SKILL_DESCRIPTORS, customMetadata=METADATA)
        stub_submission(stub, record)
        status, body = submit(handler, created)
        assert status == 200
        snapshot = stored(db, f'SUBMISSION#{body["submissionId"]}')
        assert snapshot["supplement"]["skillCommit"] == "0123456789abcdef0123456789abcdef01234567"
        assert snapshot["supplement"]["skillDigest"] == "sha256:" + "ab" * 32


def stub_submission(stub, record, error=None):
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
    if error:
        stub.add_client_error("submit_registry_record_for_approval", error, expected_params=GET)
    else:
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


@mock_aws
def test_custom_submission_takes_contact_from_the_actual_descriptor():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        definition = json.loads(CUSTOM_DESCRIPTORS["custom"]["data"])
        definition["contact"] = "new@example.com"
        record = aws_record(
            CUSTOM_DRAFT,
            descriptors={"custom": {"data": json.dumps(definition)}},
            customMetadata=META,
        )
        stub.add_response("get_registry_record", record, GET)
        _, current = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        stub_submission(stub, record)
        status, body = submit(handler, current)
        assert status == 200
        assert stored(db, f'SUBMISSION#{body["submissionId"]}')["contact"] == "new@example.com"


@pytest.mark.parametrize("metadata", [METADATA, {**META, "isA2A": True}])
@mock_aws
def test_submission_rejects_missing_or_inconsistent_isa2a(metadata):
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, transport = setup_draft(stub, registry, db)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=metadata)
        stub.add_response("get_registry_record", record, GET)
        _, current = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        stub.add_response("get_registry_record", record, GET)
        status, body = submit(handler, current)
        assert status == 400
        assert transport.requests == []
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_unknown_aws_submission_keeps_the_snapshot_and_lock_until_readback():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
        stub_submission(stub, record, error="InternalServerException")
        status, pending = submit(handler, created)
        assert status == 202
        operation_id = pending["operationId"]
        assert stored(db, f"SUBMISSION#{operation_id}") is not None
        assert stored(db, "LOCK")["operationId"] == operation_id
        # 申請結果がまだ DRAFT と見えたら申請 API を呼び直す。先の呼び出しが届いていた可能性が
        # あるため、再試行のエラーは失敗と確定せず、ロックも解除しない。
        stub.add_response("get_registry_record", record, GET)
        stub.add_client_error(
            "submit_registry_record_for_approval", "ConflictException", expected_params=GET
        )
        assert call(handler, event("GET", f"/api/operations/{operation_id}"))[0] == 202
        assert stored(db, "LOCK")["operationId"] == operation_id
        status, _ = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {
                    "expectedContentHash": created["contentHash"],
                    "draft": CUSTOM_DRAFT,
                },
            ),
        )
        assert status == 409
        stub.add_response("get_registry_record", {**record, "status": "PENDING_APPROVAL"}, GET)
        status, finished = call(handler, event("GET", f"/api/operations/{operation_id}"))
        assert status == 200
        assert finished["submissionId"] == operation_id
        assert stored(db, "LOCK") is None
        # 同じキーの再送は AWS を呼ばず確定済み結果を返す。
        assert submit(handler, created) == (200, finished)
        status, _ = submit(handler, created, reason="別の理由")
        assert status == 409
        stub.assert_no_pending_responses()


@mock_aws
def test_submission_stopped_after_the_snapshot_resumes_on_readback():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
        # スナップショットの保存後、申請 API の受け付けを確認できずに止まった
        stub_submission(stub, record, error="InternalServerException")
        status, pending = submit(handler, created)
        assert status == 202
        operation_id = pending["operationId"]
        snapshot = stored(db, f"SUBMISSION#{operation_id}")
        # 照合で DRAFT のままなら申請 API を呼び直し、承認待ちを確認して確定する
        stub.add_response("get_registry_record", record, GET)
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
        status, finished = call(handler, event("GET", f"/api/operations/{operation_id}"))
        assert status == 200
        assert finished["submissionId"] == operation_id
        assert finished["snapshotHash"] == snapshot["snapshotHash"]
        assert stored(db, f"SUBMISSION#{operation_id}") == snapshot
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_submission_refuses_a_draft_without_the_pinned_supplement(monkeypatch):
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
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
        # 連絡先などが欠けた下書きを、空文字で補ってスナップショットに固定しない
        monkeypatch.setattr(RecordDraft, "supplement", lambda self: {"contact": "a@example.com"})
        status, body = submit(handler, created)
        assert status == 400
        assert "連絡先・利用条件・接続先の参照" in body["error"]["message"]
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("before_status", ["PENDING_APPROVAL", "REJECTED"])
@mock_aws
def test_editing_invalidates_the_previous_submission_and_allows_the_same_unpublished_version(
    before_status,
):
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, transport = setup_draft(stub, registry, db, [absent(), absent()])
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
        stub_submission(stub, record)
        _, submitted = submit(handler, created)
        original = stored(db, f'SUBMISSION#{submitted["submissionId"]}')
        store = DraftStore(SUPPLEMENT_TABLE, REGISTRY_ARN, db)
        assert store.get_submission(RECORD_ID, submitted["submissionId"])["invalidated"] is False
        pending = {**record, "status": before_status}
        stub.add_response("get_registry_record", pending, GET)
        stub.add_response(
            "update_registry_record",
            {**record, "status": "UPDATING"},
            {
                **GET,
                "displayName": {"optionalValue": "決済"},
                "description": {"optionalValue": "決済を調べる"},
                "recordVersion": "1.0.0",
                "customMetadata": {"optionalValue": META},
                "descriptors": {
                    "optionalValue": {
                        "custom": {
                            "optionalValue": {
                                "data": {"optionalValue": CUSTOM_DESCRIPTORS["custom"]["data"]},
                            }
                        }
                    }
                },
            },
        )
        stub.add_response("get_registry_record", record, GET)
        status, updated = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {
                    "expectedContentHash": submitted["contentHash"],
                    "draft": CUSTOM_DRAFT,
                },
            ),
        )
        assert status == 200
        assert stored(db, "MANAGEMENT")["activeSubmissionId"] is None
        assert stored(db, f'SUBMISSION#{submitted["submissionId"]}#INVALIDATED') is not None
        assert stored(db, f'SUBMISSION#{submitted["submissionId"]}') == original
        # 公開承認（#10）が読む入口では、元の申請が無効と分かる
        assert store.get_submission(RECORD_ID, submitted["submissionId"]) == {
            **original,
            "invalidated": True,
        }
        stub_submission(stub, record)
        request = event(
            "POST",
            f"/api/records/{RECORD_ID}/submit",
            {
                "expectedContentHash": updated["contentHash"],
                "reason": "再申請",
                "relatedReferences": [],
            },
            key="resubmit",
        )
        status, resubmitted = call(handler, request)
        assert status == 200
        assert resubmitted["draft"]["recordVersion"] == "1.0.0"
        assert resubmitted["submissionId"] != submitted["submissionId"]
        assert len(transport.registry) == 0
        stub.assert_no_pending_responses()


@mock_aws
def test_submission_does_not_persist_credentials_in_the_reason_or_external_metadata():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        status, body = submit(handler, created, reason="api_key=not-a-reference")
        assert status == 400
        assert "not-a-reference" not in json.dumps(body)
        assert stored(db, "LOCK") is None
        record = aws_record(
            CUSTOM_DRAFT,
            descriptors=CUSTOM_DESCRIPTORS,
            customMetadata={**META, "password": "actual-password"},
        )
        stub.add_response("get_registry_record", record, GET)
        _, current = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
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
        status, body = submit(handler, current)
        assert status == 400
        assert "actual-password" not in json.dumps(body)
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_store_failure_after_aws_submission_is_recovered_without_submitting_twice():
    from botocore.exceptions import ClientError

    registry, db = clients()

    class FailingStore:
        armed = False

        def get_item(self, **kwargs):
            return db.get_item(**kwargs)

        def transact_write_items(self, **kwargs):
            if self.armed and any(
                item.get("Put", {}).get("Item", {}).get("sk", {}).get("S", "").endswith("#RESULT")
                for item in kwargs["TransactItems"]
            ):
                self.armed = False
                raise ClientError({"Error": {"Code": "InternalServerError"}}, "TransactWriteItems")
            return db.transact_write_items(**kwargs)

    failing = FailingStore()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, failing)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
        stub_submission(stub, record)

        def after_submit(**kwargs):
            failing.armed = True

        registry.meta.events.register(
            "after-call.agent-registry-control.SubmitRegistryRecordForApproval", after_submit
        )
        status, result = submit(handler, created)
        assert status == 202
        operation_id = result["operationId"]
        assert stored(db, "LOCK")["operationId"] == operation_id
        assert stored(db, "MANAGEMENT").get("activeSubmissionId") is None
        assert stored(db, f"SUBMISSION#{operation_id}") is not None
        stub.add_response("get_registry_record", {**record, "status": "PENDING_APPROVAL"}, GET)
        status, result = call(handler, event("GET", f"/api/operations/{operation_id}"))
        assert status == 200
        assert result["operationState"] == "SUCCEEDED"
        assert stored(db, "LOCK") is None
        assert stored(db, "MANAGEMENT")["activeSubmissionId"] == operation_id
        stub.assert_no_pending_responses()


@mock_aws
def test_submission_does_not_replace_a_known_published_version_with_stale_discovery():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db, [published("1.0.0")])
        db.update_item(
            TableName=SUPPLEMENT_TABLE,
            Key={
                "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"},
                "sk": {"S": "MANAGEMENT"},
            },
            UpdateExpression="SET publishedVersion = :v",
            ExpressionAttributeValues={":v": {"S": "2.0.0"}},
        )
        record = aws_record(
            {**CUSTOM_DRAFT, "recordVersion": "3.0.0"},
            descriptors=CUSTOM_DESCRIPTORS,
            customMetadata=META,
        )
        stub.add_response("get_registry_record", record, GET)
        _, current = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        stub_submission(stub, record)
        status, result = submit(handler, current)
        assert status == 200
        assert result["publishedVersion"] == "2.0.0"
        assert stored(db, "MANAGEMENT")["publishedVersion"] == "2.0.0"


@mock_aws
def test_a_duplicate_during_preflight_cannot_release_the_first_requests_lock():
    registry, db = clients()
    with Stubber(registry) as stub:
        handler, created, _ = setup_draft(stub, registry, db)
        record = aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META)
        stub_submission(stub, record)
        duplicates = []

        def duplicate(**kwargs):
            status, result = submit(handler, created)
            duplicates.append((status, result["operationState"]))
            assert stored(db, "LOCK") is not None

        registry.meta.events.register(
            "before-parameter-build.agent-registry-control.GetRegistry", duplicate
        )
        status, result = submit(handler, created)
        assert duplicates == [(202, "VERIFYING")]
        assert status == 200
        assert result["operationState"] == "SUCCEEDED"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()
