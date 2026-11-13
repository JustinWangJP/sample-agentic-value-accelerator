"""Admin の取り込みと所有変更を HTTP handler の境界で確認する。"""

import pytest
from botocore.stub import Stubber
from moto import mock_aws
from draft_helpers import call, clients, event, handler_for
from decision_helpers import ENV, SUPPLEMENT_TABLE
from registry_portal.app import create_curation_handler
from collections import deque
from catalog_helpers import REGISTRY_ARN, FakeTransport, FakeSecrets, json_response
from decision_helpers import GET, RECORD, stored, missing_published
from draft_helpers import RECORD_ID
from registry_portal.services.discovery import DiscoveryDependencies
import json
from botocore.exceptions import EndpointConnectionError
from catalog_helpers import summary
from decision_helpers import put, setup_pending, decide, stub_status_update
from record_fixtures import CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, CUSTOM_DEFINITION
from botocore.stub import ANY
from registry_portal.app import create_management_handler


ADMIN = "registry-admin"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/admin/records"),
        ("GET", "/api/admin/connection"),
        ("POST", "/api/records/agentABC1234/adopt"),
        ("POST", "/api/records/agentABC1234/reassign-owner"),
    ],
)
@mock_aws
def test_non_admin_is_rejected_before_aws(method, path):
    registry, db = clients()
    handler = (
        handler_for(registry, db)
        if method == "GET"
        else create_curation_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            store_client=db,
        )
    )
    with Stubber(registry):
        status, _ = call(
            handler, event(method, path, groups="registry-curator registry-publisher team-payments")
        )
    assert status == 403


def decision_for(registry, db, responses):
    transport = FakeTransport(registry=deque(responses))
    return create_curation_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
    )


def adopt(handler, **changes):
    return call(
        handler,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/adopt",
            {
                "ownerTeam": "team-payments",
                "reason": "既存レコードの管理を開始する",
                **changes,
            },
            groups=ADMIN,
        ),
    )


@mock_aws
def test_adopt_draft_is_atomic_and_replayable_and_history_is_readable():
    registry, db = clients()
    handler = decision_for(registry, db, [missing_published()])
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", RECORD, GET)
        status, result = adopt(handler)
        assert status == 200
        assert result["operationState"] == "SUCCEEDED"
        assert adopt(handler) == (status, result)
    management = stored(db, "MANAGEMENT")
    assert management["managed"] is True
    assert management["ownerTeam"] == "team-payments"
    assert "adoptedPublishedVersion" not in management
    assert "lastEditedBy" not in management
    assert stored(db, "LOCK") is None
    assert stored(db, f'AUDIT#{result["operationId"]}#INTENT')["kind"] == "ADOPT"
    assert stored(db, f'AUDIT#{result["operationId"]}#RESULT') is not None
    status, history = call(
        handler_for(registry, db), event("GET", f"/api/records/{RECORD_ID}/history", groups=ADMIN)
    )
    assert status == 200
    assert history["entries"][0]["kind"] == "ADOPT"


def registry_configuration(rules):
    return {
        "name": "registry",
        "registryId": "abcdEFGH1234",
        "registryArn": REGISTRY_ARN,
        "status": "ACTIVE",
        "createdAt": RECORD["createdAt"],
        "updatedAt": RECORD["updatedAt"],
        "discoveryConfiguration": {"authorizerType": "CUSTOM_JWT"},
        "approvalConfiguration": {"autoApprovalRules": rules},
    }


@pytest.mark.parametrize("rules,state", [([], "DISABLED"), (["ALL"], "ENABLED")])
@mock_aws
def test_connection_reports_observed_authorizer_and_approval_warning(rules, state):
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_response(
            "get_registry", registry_configuration(rules), {"registryId": REGISTRY_ARN}
        )
        status, result = call(
            handler_for(registry, db), event("GET", "/api/admin/connection", groups=ADMIN)
        )
    assert status == 200
    assert result["registryArn"] == REGISTRY_ARN
    assert result["region"] == "us-east-1"
    assert result["authorization"] == "CUSTOM_JWT"
    assert result["autoApproval"]["state"] == state
    assert bool(result["warnings"]) == bool(rules)


def public_record(**changes):
    return {
        **summary(RECORD_ID),
        "descriptors": CUSTOM_DESCRIPTORS,
        "customMetadata": {
            "environment": "dev",
            "dataClassification": "private",
            "external": "保持する",
        },
        **changes,
    }


def admin_page(stub, handler, *, managed=False, records=None, token=None):
    response = {
        "registryRecords": [
            {
                key: value
                for key, value in record.items()
                if key not in {"descriptors", "customMetadata"}
            }
            for record in (records if records is not None else [RECORD])
        ]
    }
    if token:
        response["nextToken"] = token
    stub.add_response(
        "list_registry_records", response, {"registryId": REGISTRY_ARN, "maxResults": 50}
    )
    request = event("GET", "/api/admin/records", groups=ADMIN)
    request["queryStringParameters"] = {"management": "managed" if managed else "unmanaged"}
    return call(handler, request)


@mock_aws
def test_adoption_separates_public_version_and_preserves_unknown_history():
    registry, db = clients()
    current = {
        **RECORD,
        "recordVersion": "2.0.0",
        "descriptors": {
            "custom": {"data": json.dumps({**CUSTOM_DEFINITION, "contact": "draft@example.com"})}
        },
    }
    published = public_record()
    handler = decision_for(
        registry, db, [json_response(200, {"registryRecords": [published], "errors": []})]
    )
    management = handler_for(registry, db)
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", current, GET)
        status, result = adopt(handler)
        assert status == 200
        assert result["publishedVersion"] == "1.0.0"
        assert result["a2aBackfillRequired"] is True
        _, page = admin_page(stub, management, managed=True, records=[current])
        stub.add_response("get_registry_record", current, GET)
        status, reassigned = call(
            handler,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/reassign-owner",
                {
                    "ownerTeam": "team-finance",
                    "expectedManagementHash": page["items"][0]["managementHash"],
                    "reason": "組織変更に合わせる",
                },
                groups=ADMIN,
            ),
        )
        assert status == 200
        assert reassigned["ownerTeam"] == "team-finance"
        assert call(management, event("GET", f"/api/records/{RECORD_ID}"))[0] == 403
        stub.add_response("get_registry_record", current, GET)
        assert (
            call(
                management,
                event("GET", f"/api/records/{RECORD_ID}", groups="registry-publisher team-finance"),
            )[0]
            == 200
        )
    snapshot = stored(db, "PUBLISHED#1.0.0")
    assert snapshot["contact"] == CUSTOM_DRAFT["contact"]
    assert snapshot["record"]["customMetadata"]["external"] == "保持する"
    assert "approvedBy" not in snapshot
    assert stored(db, "MANAGEMENT")["contact"] == "draft@example.com"
    assert stored(db, "MANAGEMENT")["adoptedPublishedVersion"] == "1.0.0"
    _, history = call(management, event("GET", f"/api/records/{RECORD_ID}/history", groups=ADMIN))
    assert history["entries"][0] == {
        "operationId": None,
        "kind": "APPROVE",
        "principal": None,
        "startedAt": None,
        "targetHash": None,
        "state": "UNKNOWN",
        "recordVersion": "1.0.0",
    }
    assert {entry["kind"] for entry in history["entries"]} == {"ADOPT", "APPROVE", "REASSIGN_OWNER"}


@mock_aws
def test_empty_filtered_page_keeps_next_token_and_unmanaged_read_failures_are_not_empty():
    registry, db = clients()
    handler = handler_for(registry, db)
    put(db, "MANAGEMENT", {"managed": True, "ownerTeam": "team-payments"})
    with Stubber(registry) as stub:
        status, page = admin_page(stub, handler, token="next-page")
        assert status == 200
        assert page == {"items": [], "nextToken": "next-page"}
        stub.add_client_error(
            "list_registry_records",
            "AccessDeniedException",
            expected_params={"registryId": REGISTRY_ARN, "maxResults": 50},
        )
        assert call(handler, event("GET", "/api/admin/records", groups=ADMIN))[0] == 502
    put(db, "MANAGEMENT", {"managed": True, "ownerTeam": 123})
    with Stubber(registry) as stub:
        assert admin_page(stub, handler)[0] == 502


@pytest.mark.parametrize(
    "code", ["AccessDeniedException", "ThrottlingException", "InternalServerException"]
)
@mock_aws
def test_connection_error_preserves_configured_location_and_marks_unknown(code):
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_client_error("get_registry", code, expected_params={"registryId": REGISTRY_ARN})
        status, result = call(
            handler_for(registry, db), event("GET", "/api/admin/connection", groups=ADMIN)
        )
    assert status == 200
    assert result["registryArn"] == REGISTRY_ARN
    assert result["authorization"] is None
    assert result["connection"]["state"] == "ERROR"
    assert result["autoApproval"]["state"] == "UNKNOWN"


@mock_aws
def test_lock_or_existing_snapshot_prevents_partial_adoption():
    registry, db = clients()
    handler = decision_for(
        registry, db, [json_response(200, {"registryRecords": [public_record()], "errors": []})]
    )
    put(db, "LOCK", {"operationId": "other"})
    with Stubber(registry):
        assert adopt(handler)[0] == 409
    assert stored(db, "MANAGEMENT") is None
    db.delete_item(
        TableName=SUPPLEMENT_TABLE,
        Key={"pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"}, "sk": {"S": "LOCK"}},
    )
    put(db, "PUBLISHED#1.0.0", {"contact": "既存"})
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", RECORD, GET)
        assert adopt(handler)[0] == 409
    assert stored(db, "MANAGEMENT") is None
    assert stored(db, "PUBLISHED#1.0.0")["contact"] == "既存"


@mock_aws
def test_discovery_error_never_becomes_no_public_version():
    registry, db = clients()
    handler = decision_for(
        registry,
        db,
        [
            json_response(
                200,
                {
                    "registryRecords": [],
                    "errors": [{"recordId": RECORD_ID, "errorCode": "ACCESS_DENIED"}],
                },
            )
        ],
    )
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", RECORD, GET)
        assert adopt(handler)[0] == 502
    assert stored(db, "MANAGEMENT") is None


@mock_aws
def test_owner_change_rejects_stale_token_and_invalidates_pending_decision_hash():
    registry, db = clients()
    with Stubber(registry) as stub:
        setup = setup_pending(stub, registry, db)
        handler = setup["decision"]
        _, page = admin_page(stub, setup["management"], managed=True)
        token = page["items"][0]["managementHash"]
        request = {
            "ownerTeam": "team-finance",
            "expectedManagementHash": token,
            "reason": "組織変更",
        }
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        assert (
            call(
                handler,
                event("POST", f"/api/records/{RECORD_ID}/reassign-owner", request, groups=ADMIN),
            )[0]
            == 200
        )
        assert (
            call(
                handler,
                event(
                    "POST",
                    f"/api/records/{RECORD_ID}/reassign-owner",
                    request,
                    groups=ADMIN,
                    key="different",
                ),
            )[0]
            == 409
        )
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, _ = decide(handler, snapshot_hash=setup["snapshot"]["snapshotHash"])
        assert status == 409


@mock_aws
def test_backfill_preserves_existing_metadata_through_edit_submit_and_new_publication():
    registry, db = clients()
    published = public_record()
    adopted = decision_for(
        registry, db, [json_response(200, {"registryRecords": [published], "errors": []})]
    )
    transport = FakeTransport(
        registry=deque([json_response(200, {"registryRecords": [published], "errors": []})])
    )
    management = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        supplement_store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
    )
    before = {**RECORD, "status": "APPROVED", "customMetadata": published["customMetadata"]}
    changed = {**CUSTOM_DRAFT, "recordVersion": "2.0.0"}
    after = {
        **RECORD,
        "recordVersion": "2.0.0",
        "customMetadata": {
            **published["customMetadata"],
            "ownerTeam": "team-payments",
            "isA2A": False,
        },
    }
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", before, GET)
        assert adopt(adopted)[0] == 200
        stub.add_response("get_registry_record", before, GET)
        _, view = call(management, event("GET", f"/api/records/{RECORD_ID}"))
        stub.add_response("get_registry_record", before, GET)
        stub.add_response(
            "update_registry_record",
            {**after, "status": "UPDATING"},
            {
                **GET,
                "displayName": {"optionalValue": changed["displayName"]},
                "description": {"optionalValue": changed["description"]},
                "recordVersion": "2.0.0",
                "descriptors": ANY,
                "customMetadata": {"optionalValue": after["customMetadata"]},
            },
        )
        stub.add_response("get_registry_record", after, GET)
        status, edited = call(
            management,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": changed, "expectedContentHash": view["contentHash"]},
            ),
        )
        assert status == 200
        stub.add_response("get_registry_record", after, GET)
        stub.add_response("get_registry", registry_configuration([]), {"registryId": REGISTRY_ARN})
        stub.add_response(
            "submit_registry_record_for_approval",
            {
                "registryArn": REGISTRY_ARN,
                "recordArn": RECORD["recordArn"],
                "recordId": RECORD_ID,
                "status": "PENDING_APPROVAL",
                "updatedAt": RECORD["updatedAt"],
            },
            GET,
        )
        pending = {**after, "status": "PENDING_APPROVAL"}
        stub.add_response("get_registry_record", pending, GET)
        status, submitted = call(
            management,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": edited["contentHash"],
                    "reason": "検索用情報を補完する",
                    "relatedReferences": [],
                },
                key="backfill-submit",
                sub="bob",
            ),
        )
        assert status == 200
        snapshot = stored(db, f'SUBMISSION#{submitted["submissionId"]}')
        stub.add_response("get_registry_record", pending, GET)
        stub_status_update(stub, record=pending)
        stub.add_response("get_registry_record", {**after, "status": "APPROVED"}, GET)
        assert decide(adopted, snapshot_hash=snapshot["snapshotHash"])[0] == 200
    assert stored(db, "MANAGEMENT")["adoptedPublishedVersion"] == "1.0.0"
    assert stored(db, "PUBLISHED#2.0.0")["record"]["customMetadata"]["external"] == "保持する"
    _, history = call(management, event("GET", f"/api/records/{RECORD_ID}/history", groups=ADMIN))
    assert history["entries"][0]["state"] == "UNKNOWN"


class InterruptedTransaction:
    def __init__(self, client, *, commit=False, before=None):
        self.client = client
        self.commit = commit
        self.before = before

    def __getattr__(self, name):
        return getattr(self.client, name)

    def transact_write_items(self, **kwargs):
        if self.before:
            self.before()
            return self.client.transact_write_items(**kwargs)
        if self.commit:
            self.client.transact_write_items(**kwargs)
        raise EndpointConnectionError(endpoint_url="https://dynamodb.example.com")


@pytest.mark.parametrize("commit,expected", [(True, 200), (False, 503)])
@mock_aws
def test_lost_transaction_response_reads_receipt_without_false_failure(commit, expected):
    registry, db = clients()
    handler = decision_for(
        registry, InterruptedTransaction(db, commit=commit), [missing_published()]
    )
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", RECORD, GET)
        status, result = adopt(handler)
        assert status == expected
        if commit:
            assert adopt(handler) == (200, result)
    assert (stored(db, "MANAGEMENT") is not None) == commit


@mock_aws
def test_concurrent_adoption_cannot_overwrite_another_owner_or_leave_audit_fragments():
    registry, db = clients()
    competing = {"managed": True, "ownerTeam": "team-other"}
    handler = decision_for(
        registry,
        InterruptedTransaction(db, before=lambda: put(db, "MANAGEMENT", competing)),
        [missing_published()],
    )
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", RECORD, GET)
        assert adopt(handler)[0] == 409
    assert stored(db, "MANAGEMENT")["ownerTeam"] == "team-other"
    _, history = call(
        handler_for(registry, db), event("GET", f"/api/records/{RECORD_ID}/history", groups=ADMIN)
    )
    assert history["entries"] == []


@mock_aws
def test_unmanaged_publisher_cannot_edit_or_submit_before_adoption():
    registry, db = clients()
    handler = handler_for(registry, db)
    with Stubber(registry):
        for method, suffix in [("PATCH", ""), ("POST", "/submit")]:
            assert (
                call(
                    handler,
                    event(method, f"/api/records/{RECORD_ID}{suffix}", {"draft": CUSTOM_DRAFT}),
                )[0]
                == 403
            )


@mock_aws
def test_adopted_record_without_metadata_can_be_read_and_completed_by_its_owner():
    registry, db = clients()
    original = {**RECORD, "customMetadata": {"external": "保持する"}}
    handler = decision_for(registry, db, [missing_published()])
    management = handler_for(registry, db)
    completed = {**RECORD, "customMetadata": {**RECORD["customMetadata"], "external": "保持する"}}
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", original, GET)
        assert adopt(handler)[0] == 200
        stub.add_response("get_registry_record", original, GET)
        status, view = call(management, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        assert view["draft"]["environment"] == "dev"
        assert view["draft"]["dataClassification"] == "private"
        assert "初期値" in view["initializationNotice"]
        stub.add_response("get_registry_record", original, GET)
        stub.add_response(
            "update_registry_record",
            {**completed, "status": "UPDATING"},
            {
                **GET,
                "displayName": {"optionalValue": CUSTOM_DRAFT["displayName"]},
                "description": {"optionalValue": CUSTOM_DRAFT["description"]},
                "recordVersion": "1.0.0",
                "descriptors": ANY,
                "customMetadata": {"optionalValue": completed["customMetadata"]},
            },
        )
        stub.add_response("get_registry_record", completed, GET)
        status, result = call(
            management,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {
                    "draft": view["draft"],
                    "expectedContentHash": view["contentHash"],
                },
            ),
        )
        assert status == 200
        assert "initializationNotice" not in result
        stub.assert_no_pending_responses()


@mock_aws
def test_adoption_never_replaces_explicit_invalid_classification_with_default():
    registry, db = clients()
    original = {
        **RECORD,
        "customMetadata": {"environment": "dev", "dataClassification": "confidential"},
    }
    handler = decision_for(registry, db, [missing_published()])
    with Stubber(registry) as stub:
        stub.add_response("get_registry_record", original, GET)
        assert adopt(handler)[0] == 200
        stub.add_response("get_registry_record", original, GET)
        assert call(handler_for(registry, db), event("GET", f"/api/records/{RECORD_ID}"))[0] != 200
