import json
from datetime import datetime, timezone

import pytest
from botocore.stub import ANY, Stubber
from moto import mock_aws

from catalog_helpers import ENV, ISSUER, REGISTRY_ARN, SUPPLEMENT_TABLE
from draft_helpers import (
    RECORD_ARN,
    RECORD_ID,
    aws_record,
    call,
    clients,
    event,
    handler_for,
    management_item,
)
from registry_portal.app import create_management_handler


def test_viewer_cannot_create_a_draft():
    status, body = call(create_management_handler(ENV), event(groups=""))
    assert status == 403
    assert body["error"]["code"] == "FORBIDDEN"


DRAFT = {
    "name": "payments-agent",
    "recordType": "AGENT",
    "descriptorType": "custom",
    "displayName": "決済 Agent",
    "description": "決済を調べる",
    "recordVersion": "1.0.0",
    "ownerTeam": "team-payments",
    "environment": "dev",
    "dataClassification": "private",
    "purpose": "決済状況の照会",
    "connectionReference": "https://example.com/payments",
    "authentication": "Cognito JWT",
    "contact": "payments@example.com",
    "termsOfUse": "社内利用のみ",
}
DEFINITION = {
    key: DRAFT[key]
    for key in (
        "purpose",
        "connectionReference",
        "authentication",
        "contact",
        "termsOfUse",
    )
}
DESCRIPTORS = {
    "custom": {
        "data": json.dumps(DEFINITION, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    }
}
METADATA = {
    "ownerTeam": "team-payments",
    "environment": "dev",
    "dataClassification": "private",
    "isA2A": False,
}


def record(**changes):
    """custom Agent の GetRegistryRecord の応答。"""
    return aws_record(DRAFT, **{"descriptors": DESCRIPTORS, "customMetadata": METADATA, **changes})


def items_with_sk(db, fragment):
    """ソートキーに fragment を含む補助テーブルの項目（DynamoDB の型付き表現）。"""
    return [
        item for item in db.scan(TableName=SUPPLEMENT_TABLE)["Items"] if fragment in item["sk"]["S"]
    ]


def audit_items_ending_with(db, suffix):
    return [item for item in items_with_sk(db, "AUDIT#") if item["sk"]["S"].endswith(suffix)]


def stub_create(stub):
    stub.add_response(
        "create_registry_record",
        {"recordArn": RECORD_ARN, "status": "CREATING"},
        {
            "registryId": REGISTRY_ARN,
            "name": DRAFT["name"],
            "displayName": DRAFT["displayName"],
            "description": DRAFT["description"],
            "recordType": "AGENT",
            "recordVersion": "1.0.0",
            "descriptors": DESCRIPTORS,
            "customMetadata": METADATA,
            "clientToken": ANY,
        },
    )
    stub.add_response(
        "get_registry_record",
        record(),
        {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
    )


@mock_aws
def test_publisher_creates_custom_agent_once_and_replays_the_same_result():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        status, created = call(handler, event(body=DRAFT))
        assert status == 201
        assert created["recordId"] == RECORD_ID
        assert created["draft"] == DRAFT
        assert len(created["contentHash"]) == 64
        assert created["hasDraft"] is True
        assert created["status"] == "DRAFT"
        management = db.get_item(
            TableName=SUPPLEMENT_TABLE,
            Key={
                "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"},
                "sk": {"S": "MANAGEMENT"},
            },
        )["Item"]
        assert management["contact"] == {"S": "payments@example.com"}
        assert management["termsOfUse"] == {"S": "社内利用のみ"}
        assert management["connectionReference"] == {"S": "https://example.com/payments"}
        assert call(handler, event(body=DRAFT)) == (201, created)
        stub.assert_no_pending_responses()


@mock_aws
def test_publisher_edits_with_expected_hash_and_update_optional_wrappers():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        stub.add_response(
            "get_registry_record",
            record(),
            {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
        )
        changed = {**DRAFT, "description": "新しい説明"}
        stub.add_response(
            "update_registry_record",
            record(description="新しい説明", status="UPDATING"),
            {
                "registryId": REGISTRY_ARN,
                "recordId": RECORD_ID,
                "displayName": {"optionalValue": DRAFT["displayName"]},
                "description": {"optionalValue": "新しい説明"},
                "recordVersion": "1.0.0",
                "customMetadata": {"optionalValue": METADATA},
                "descriptors": {
                    "optionalValue": {
                        "custom": {
                            "optionalValue": {
                                "data": {"optionalValue": DESCRIPTORS["custom"]["data"]},
                            }
                        }
                    }
                },
            },
        )
        stub.add_response(
            "get_registry_record",
            record(description="新しい説明"),
            {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
        )
        status, updated = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": changed, "expectedContentHash": created["contentHash"]},
            ),
        )
        assert status == 200
        assert updated["draft"]["description"] == "新しい説明"
        assert updated["contentHash"] != created["contentHash"]
        stub.assert_no_pending_responses()


@mock_aws
def test_other_team_cannot_read_or_edit_a_draft_without_fetching_aws_content():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        call(handler, event(body=DRAFT))
        for method in ["GET", "PATCH"]:
            status, body = call(
                handler,
                event(
                    method,
                    f"/api/records/{RECORD_ID}",
                    groups="registry-publisher team-cards",
                ),
            )
            assert status == 403
            assert body["error"]["code"] == "FORBIDDEN"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "groups",
    [
        "registry-publisher",
        "registry-curator team-payments",
        "registry-admin team-payments",
    ],
)
def test_only_a_publisher_with_a_team_can_write(groups):
    status, body = call(create_management_handler(ENV), event(groups=groups))
    assert status == 403
    assert body["error"]["code"] == "FORBIDDEN"


@mock_aws
def test_missing_key_and_invalid_input_do_not_write_to_aws():
    registry, db = clients()
    with Stubber(registry):
        handler = handler_for(registry, db)
        assert call(handler, event(body=DRAFT, key=""))[0] == 400
        for change in [
            {"environment": "prod"},
            {"recordVersion": "latest"},
            # AWS の RegistryRecordVersion は `+` を受け付けないため、ビルドメタデータは送る前に拒否する
            {"recordVersion": "1.0.0+build.1"},
            {"dataClassification": "secret"},
            {"status": "APPROVED"},
            {"purpose": ""},
        ]:
            assert call(handler, event(body={**DRAFT, **change}))[0] == 400
        assert call(handler, event(body={**DRAFT, "ownerTeam": "team-cards"}))[0] == 403


@mock_aws
def test_stale_hash_rejects_update_and_releases_lock_for_a_fresh_request():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        for _ in range(2):
            stub.add_response(
                "get_registry_record",
                record(),
                {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
            )
            status, body = call(
                handler,
                event(
                    "PATCH",
                    f"/api/records/{RECORD_ID}",
                    {"draft": DRAFT, "expectedContentHash": "0" * 64},
                ),
            )
            assert status == 409
            assert body["error"]["code"] == "CONTENT_CONFLICT"
        assert call(handler, event(body={**DRAFT, "description": "異なる入力"}))[0] == 409
        assert (
            call(handler, event("PATCH", f"/api/records/{RECORD_ID}", {"draft": DRAFT}))[0] == 400
        )
        assert created["draft"] == DRAFT
        stub.assert_no_pending_responses()


@mock_aws
def test_unknown_create_can_retry_with_the_same_aws_client_token():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_client_error(
            "create_registry_record", "InternalServerException", http_status_code=500
        )
        handler = handler_for(registry, db)
        status, pending = call(handler, event(body=DRAFT))
        assert status == 202
        assert pending["operationState"] == "VERIFYING"
        stub_create(stub)
        status, created = call(handler, event(body=DRAFT))
        assert status == 201
        assert created["operationId"] == pending["operationId"]
        assert created["recordId"] == RECORD_ID
        stub.assert_no_pending_responses()


@mock_aws
def test_create_timeout_can_be_retried_with_identical_input_and_client_token():
    from botocore.exceptions import ReadTimeoutError

    registry, db = clients()
    create = registry.create_registry_record
    requests = []

    def timeout_once(**params):
        requests.append(params)
        if len(requests) == 1:
            raise ReadTimeoutError(endpoint_url="https://example.com")
        return create(**params)

    registry.create_registry_record = timeout_once
    with Stubber(registry) as stub:
        handler = handler_for(registry, db)
        status, pending = call(handler, event(body=DRAFT))
        assert status == 202
        for _ in range(3):
            assert (
                call(handler, event("GET", f"/api/operations/{pending['operationId']}"))[0] == 202
            )
        stub_create(stub)
        status, saved = call(handler, event(body=DRAFT))
        assert status == 201
        assert requests[0] == requests[1]
        assert requests[1]["clientToken"] == pending["operationId"] == saved["operationId"]
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "error_code",
    [
        "ValidationException",
        "AccessDeniedException",
        "ConflictException",
        "ResourceNotFoundException",
    ],
)
@mock_aws
def test_rejected_update_releases_lock_so_publisher_can_correct_and_save(error_code):
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        request = event(
            "PATCH",
            f"/api/records/{RECORD_ID}",
            {
                "draft": {**DRAFT, "description": "修正する説明"},
                "expectedContentHash": created["contentHash"],
            },
        )
        stub.add_response("get_registry_record", record())
        stub.add_client_error("update_registry_record", error_code, http_status_code=400)
        status, rejected = call(handler, request)
        assert status == 400
        assert rejected["error"]["code"] == "VALIDATION_ERROR"
        stub.add_response("get_registry_record", record())
        stub.add_response("update_registry_record", record(status="UPDATING"))
        stub.add_response("get_registry_record", record(description="修正する説明"))
        status, saved = call(handler, request)
        assert status == 200
        assert saved["operationState"] == "SUCCEEDED"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("error_code", "http_status", "status", "code"),
    [
        ("ServiceQuotaExceededException", 402, 400, "VALIDATION_ERROR"),
        # 混雑は入力の誤りではないため、400 ではなく 429 で「時間をおいて再度保存」を促す
        ("ThrottlingException", 429, 429, "REGISTRY_THROTTLED"),
    ],
)
@mock_aws
def test_rejected_create_releases_name_for_a_new_operation(error_code, http_status, status, code):
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_client_error(
            "create_registry_record",
            error_code,
            http_status_code=http_status,
            response_meta={"RequestId": "aws-request-1"},
        )
        handler = handler_for(registry, db)
        rejected_status, rejected = call(handler, event(body=DRAFT))
        assert (rejected_status, rejected["error"]["code"]) == (status, code)
        assert call(handler, event(body=DRAFT))[0] == 409
        stub_create(stub)
        assert call(handler, event(body=DRAFT, key="new-operation"))[0] == 201
        stub.assert_no_pending_responses()
    results = audit_items_ending_with(db, "#RESULT")
    assert results[0]["awsRequestId"] == {"S": "aws-request-1"}


@mock_aws
def test_throttled_update_is_429_and_releases_lock_for_saving_again():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        request = event(
            "PATCH",
            f"/api/records/{RECORD_ID}",
            {"draft": DRAFT, "expectedContentHash": created["contentHash"]},
        )
        stub.add_response("get_registry_record", record())
        stub.add_client_error("update_registry_record", "ThrottlingException", http_status_code=429)
        status, throttled = call(handler, request)
        assert status == 429
        assert throttled["error"]["code"] == "REGISTRY_THROTTLED"
        assert "時間をおいて" in throttled["error"]["message"]
        stub.add_response("get_registry_record", record())
        stub.add_response("update_registry_record", record(status="UPDATING"))
        stub.add_response("get_registry_record", record())
        assert call(handler, request)[0] == 200
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("kind", ["CREATE", "UPDATE"])
@mock_aws
def test_terminal_aws_failure_is_saved_and_releases_all_locks(kind):
    registry, db = clients()
    with Stubber(registry) as stub:
        handler = handler_for(registry, db)
        if kind == "CREATE":
            stub.add_response(
                "create_registry_record", {"recordArn": RECORD_ARN, "status": "CREATING"}
            )
            stub.add_response("get_registry_record", record(status="CREATING"))
            status, pending = call(handler, event(body=DRAFT))
        else:
            stub_create(stub)
            _, created = call(handler, event(body=DRAFT))
            stub.add_response("get_registry_record", record())
            stub.add_response("update_registry_record", record(status="UPDATING"))
            stub.add_response("get_registry_record", record(status="UPDATING"))
            status, pending = call(
                handler,
                event(
                    "PATCH",
                    f"/api/records/{RECORD_ID}",
                    {"draft": DRAFT, "expectedContentHash": created["contentHash"]},
                ),
            )
        assert status == 202
        stub.add_response("get_registry_record", record(status=f"{kind}_FAILED"))
        operation_request = event("GET", f"/api/operations/{pending['operationId']}")
        status, failed = call(handler, operation_request)
        assert status == 200
        assert failed["operationState"] == "FAILED"
        assert call(handler, operation_request) == (200, failed)
        if kind == "CREATE":
            stub_create(stub)
            assert call(handler, event(body=DRAFT, key="retry-after-failure"))[0] == 201
        stub.add_response("get_registry_record", record())
        status, detail = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        stub.add_response("get_registry_record", record())
        stub.add_response("update_registry_record", record(status="UPDATING"))
        stub.add_response("get_registry_record", record())
        status, saved = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": DRAFT, "expectedContentHash": detail["contentHash"]},
            ),
        )
        assert status == 200
        assert saved["operationState"] == "SUCCEEDED"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "invalid",
    [
        {"ownerTeam": {"L": [{"S": "team-payments"}]}},
        {"managed": {"S": "true"}},
        {"contact": {"N": "123"}},
        {"publishedVersion": {"L": []}},
        {"lastEditedBy": {"S": "alice"}},
    ],
)
@mock_aws
def test_invalid_management_is_an_http_error_instead_of_empty_or_forbidden(invalid):
    registry, db = clients()
    db.put_item(
        TableName=SUPPLEMENT_TABLE,
        Item={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"},
            "sk": {"S": "MANAGEMENT"},
            "managed": {"BOOL": True},
            "ownerTeam": {"S": "team-payments"},
            **invalid,
        },
    )
    with Stubber(registry) as stub:
        summary = {
            key: value
            for key, value in record().items()
            if key not in ("descriptors", "customMetadata")
        }
        stub.add_response("list_registry_records", {"registryRecords": [summary]})
        handler = handler_for(registry, db)
        for request in [event("GET", "/api/records"), event("GET", f"/api/records/{RECORD_ID}")]:
            status, body = call(handler, request)
            assert status == 502
            assert body["error"]["code"] == "REGISTRY_UNAVAILABLE"
            assert "管理情報" in body["error"]["message"]
        stub.assert_no_pending_responses()


@mock_aws
def test_creating_draft_is_visible_and_record_lock_survives_an_old_ttl():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_response("create_registry_record", {"recordArn": RECORD_ARN, "status": "CREATING"})
        stub.add_response("get_registry_record", record(status="CREATING"))
        handler = handler_for(registry, db)
        status, pending = call(handler, event(body=DRAFT))
        assert status == 202
        stub.add_response("get_registry_record", record(status="CREATING"))
        status, detail = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        assert detail["status"] == "CREATING"
        db.update_item(
            TableName=SUPPLEMENT_TABLE,
            Key={
                "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"},
                "sk": {"S": "LOCK"},
            },
            UpdateExpression="SET expiresAt = :ttl",
            ExpressionAttributeValues={":ttl": {"N": "1"}},
        )
        status, _ = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": DRAFT, "expectedContentHash": detail["contentHash"]},
            ),
        )
        assert status == 409
        stub.add_response("get_registry_record", record())
        status, finished = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 200
        assert finished["operationState"] == "SUCCEEDED"
        stub.assert_no_pending_responses()


@mock_aws
def test_management_list_keeps_the_published_version_beside_a_new_draft_and_filters_other_teams():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        stub.add_response("get_registry_record", record(status="APPROVED"))
        changed = {**DRAFT, "recordVersion": "2.0.0"}
        stub.add_response(
            "update_registry_record", record(recordVersion="2.0.0", status="UPDATING")
        )
        stub.add_response("get_registry_record", record(recordVersion="2.0.0"))
        status, updated = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": changed, "expectedContentHash": created["contentHash"]},
            ),
        )
        assert status == 200
        assert updated["publishedVersion"] == "1.0.0"
        summary = {
            key: value
            for key, value in record(recordVersion="2.0.0", status="UPDATE_FAILED").items()
            if key not in ("descriptors", "customMetadata")
        }
        foreign = {
            **summary,
            "recordId": "otherABC1234",
            "recordArn": f"{REGISTRY_ARN}/record/otherABC1234",
            "name": "他チームの機密名",
        }
        stub.add_response(
            "list_registry_records",
            {"registryRecords": [summary, foreign], "nextToken": "next-page"},
            {
                "registryId": REGISTRY_ARN,
                "maxResults": 20,
                "filters": [{"name": "recordType", "values": ["AGENT", "MCP", "SKILL"]}],
            },
        )
        status, page = call(handler, event("GET", "/api/records"))
        assert status == 200
        assert page["nextToken"] == "next-page"
        assert len(page["items"]) == 1
        assert page["items"][0]["publishedVersion"] == "1.0.0"
        assert page["items"][0]["hasDraft"] is True
        assert page["items"][0]["status"] == "UPDATE_FAILED"
        assert "他チーム" not in json.dumps(page, ensure_ascii=False)
        stub.assert_no_pending_responses()


@mock_aws
def test_aws_success_followed_by_store_failure_keeps_name_locked_and_retries_same_token():
    from botocore.exceptions import ClientError

    registry, db = clients()
    fail_once = [True]

    def fail_pending_write(params, **kwargs):
        if fail_once[0] and any(
            item.get("Put", {}).get("Item", {}).get("state") == {"S": "VERIFYING"}
            for item in params["TransactItems"]
        ):
            fail_once[0] = False
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": "補助ストア障害"}},
                "TransactWriteItems",
            )

    db.meta.events.register(
        "before-parameter-build.dynamodb.TransactWriteItems", fail_pending_write
    )
    with Stubber(registry) as stub:
        stub.add_response("create_registry_record", {"recordArn": RECORD_ARN, "status": "CREATING"})
        handler = handler_for(registry, db)
        status, pending = call(handler, event(body=DRAFT))
        assert status == 202
        assert pending["operationState"] == "VERIFYING"
        assert call(handler, event(body=DRAFT, key="different-key"))[0] == 409
        stub_create(stub)
        status, result = call(handler, event(body=DRAFT))
        assert status == 201
        assert result["operationId"] == pending["operationId"]
        stub.assert_no_pending_responses()


@mock_aws
def test_uncertain_update_is_not_reissued_and_can_be_verified_without_releasing_lock_early():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        changed = {**DRAFT, "description": "結果確認を待つ説明"}
        stub.add_response("get_registry_record", record())
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        request = event(
            "PATCH",
            f"/api/records/{RECORD_ID}",
            {"draft": changed, "expectedContentHash": created["contentHash"]},
        )
        status, pending = call(handler, request)
        assert status == 202
        assert call(handler, request)[0] == 409
        stub.add_response("get_registry_record", record(description=changed["description"]))
        status, verified = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 200
        assert verified["operationState"] == "SUCCEEDED"
        assert verified["draft"]["description"] == changed["description"]
        stub.assert_no_pending_responses()


@mock_aws
def test_same_team_member_can_read_draft_but_unmanaged_record_is_forbidden():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        call(handler, event(body=DRAFT))
        request = event("GET", f"/api/records/{RECORD_ID}")
        request["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "bob"
        stub.add_response("get_registry_record", record())
        status, detail = call(handler, request)
        assert status == 200
        assert detail["draft"]["contact"] == "payments@example.com"
        assert (
            call(
                handler,
                event(
                    "PATCH",
                    "/api/records/unmanaged123",
                    {"draft": DRAFT, "expectedContentHash": detail["contentHash"]},
                ),
            )[0]
            == 403
        )
        stub.assert_no_pending_responses()


@mock_aws
def test_list_keeps_published_version_while_update_is_still_processing():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        stub.add_response("get_registry_record", record(status="APPROVED"))
        stub.add_response(
            "update_registry_record", record(status="UPDATING", recordVersion="2.0.0")
        )
        stub.add_response("get_registry_record", record(status="UPDATING", recordVersion="2.0.0"))
        status, pending = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {
                    "draft": {**DRAFT, "recordVersion": "2.0.0"},
                    "expectedContentHash": created["contentHash"],
                },
            ),
        )
        assert status == 202
        summary = {
            key: value
            for key, value in record(recordVersion="2.0.0", status="UPDATING").items()
            if key not in ("descriptors", "customMetadata")
        }
        stub.add_response("list_registry_records", {"registryRecords": [summary]})
        status, page = call(handler, event("GET", "/api/records"))
        assert status == 200
        assert page["items"][0]["publishedVersion"] == "1.0.0"
        assert page["items"][0]["hasDraft"] is True
        assert page["items"][0]["operationId"] == pending["operationId"]


@mock_aws
def test_supplement_only_change_invalidates_expected_content_hash():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        db.update_item(
            TableName=SUPPLEMENT_TABLE,
            Key={
                "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"},
                "sk": {"S": "MANAGEMENT"},
            },
            UpdateExpression="SET contact = :contact",
            ExpressionAttributeValues={":contact": {"S": "new@example.com"}},
        )
        stub.add_response("get_registry_record", record())
        status, body = call(
            handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": DRAFT, "expectedContentHash": created["contentHash"]},
            ),
        )
        assert status == 409
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        stub.add_response("get_registry_record", record())
        status, latest = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        # 下書きは custom descriptor の中身を正本として読み、補助ストアの写しでは上書きしない
        assert latest["draft"]["contact"] == DRAFT["contact"]
        assert latest["contentHash"] != created["contentHash"]
        stub.assert_no_pending_responses()


@mock_aws
def test_overlapping_create_does_not_reopen_a_completed_operation():
    registry, db = clients()
    overlapping = [True]
    nested = []
    tokens = []
    handler = handler_for(registry, db)

    def capture_token(params, **kwargs):
        tokens.append(params["clientToken"])

    def finish_other_request_first(params, **kwargs):
        if overlapping[0] and any(
            item.get("Put", {}).get("Item", {}).get("state") == {"S": "VERIFYING"}
            for item in params["TransactItems"]
        ):
            overlapping[0] = False
            nested.append(call(handler, event(body=DRAFT)))

    registry.meta.events.register(
        "before-parameter-build.agent-registry-control.CreateRegistryRecord",
        capture_token,
    )
    db.meta.events.register(
        "before-parameter-build.dynamodb.TransactWriteItems", finish_other_request_first
    )
    with Stubber(registry) as stub:
        stub.add_response("create_registry_record", {"recordArn": RECORD_ARN, "status": "CREATING"})
        stub_create(stub)
        assert call(handler, event(body=DRAFT))[0] == 202
        assert nested[0][0] == 201
        assert call(handler, event(body=DRAFT)) == nested[0]
        assert len(tokens) == 2
        assert tokens[0] == tokens[1]
        stub.assert_no_pending_responses()


@mock_aws
def test_completion_store_failure_keeps_lock_until_operation_can_be_verified():
    from botocore.exceptions import ClientError

    registry, db = clients()
    fail_once = [True]

    def fail_completion(params, **kwargs):
        if fail_once[0] and any(
            item.get("Put", {}).get("Item", {}).get("state") == {"S": "SUCCEEDED"}
            for item in params["TransactItems"]
        ):
            fail_once[0] = False
            raise ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "補助ストア障害"}},
                "TransactWriteItems",
            )

    db.meta.events.register("before-parameter-build.dynamodb.TransactWriteItems", fail_completion)
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        status, pending = call(handler, event(body=DRAFT))
        assert status == 202
        assert call(handler, event(body=DRAFT, key="different-key"))[0] == 409
        assert (
            call(
                handler,
                event(
                    "PATCH",
                    f"/api/records/{RECORD_ID}",
                    {"draft": DRAFT, "expectedContentHash": "0" * 64},
                ),
            )[0]
            == 409
        )
        stub.add_response("get_registry_record", record())
        status, verified = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 200
        assert verified["operationState"] == "SUCCEEDED"
        stub.assert_no_pending_responses()


def fail_dynamodb(db, operation, code="InternalServerError"):
    """補助ストアの指定した操作を、次の1回だけ失敗させる。"""
    from botocore.exceptions import ClientError

    remaining = [True]

    def fail(params, **kwargs):
        if remaining[0]:
            remaining[0] = False
            raise ClientError({"Error": {"Code": code, "Message": "補助ストア障害"}}, operation)

    db.meta.events.register(f"before-parameter-build.dynamodb.{operation}", fail)


def patch(created, draft):
    return event(
        "PATCH",
        f"/api/records/{RECORD_ID}",
        {"draft": draft, "expectedContentHash": created["contentHash"]},
    )


@mock_aws
def test_operation_lookup_reports_store_failure_instead_of_verifying():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_client_error(
            "create_registry_record", "InternalServerException", http_status_code=500
        )
        handler = handler_for(registry, db)
        _, pending = call(handler, event(body=DRAFT))
        fail_dynamodb(db, "GetItem")
        status, body = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 502
        assert body["error"]["code"] == "REGISTRY_UNAVAILABLE"
        stub.assert_no_pending_responses()


@mock_aws
def test_operation_lookup_of_unknown_id_is_forbidden_even_while_registry_fails():
    registry, db = clients()
    handler = handler_for(registry, db)
    status, body = call(handler, event("GET", f"/api/operations/{'a' * 32}"))
    assert status == 403
    assert body["error"]["code"] == "FORBIDDEN"


@mock_aws
def test_operation_lookup_keeps_verifying_when_registry_cannot_be_read():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        stub.add_response("get_registry_record", record())
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        _, pending = call(handler, patch(created, {**DRAFT, "description": "確認待ち"}))
        stub.add_client_error(
            "get_registry_record", "InternalServerException", http_status_code=500
        )
        status, body = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 202
        assert body["operationState"] == "VERIFYING"
        stub.assert_no_pending_responses()


@mock_aws
def test_update_that_never_reached_aws_is_failed_and_releases_the_record_lock():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        changed = {**DRAFT, "description": "届かなかった説明"}
        stub.add_response("get_registry_record", record())
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        status, pending = call(handler, patch(created, changed))
        assert status == 202
        # 更新前の内容のまま処理中でもなければ、更新は適用されなかったと確定する
        stub.add_response("get_registry_record", record())
        status, failed = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 200
        assert failed["operationState"] == "FAILED"
        stub.add_response("get_registry_record", record())
        stub.add_response("update_registry_record", record(status="UPDATING"))
        stub.add_response("get_registry_record", record(description=changed["description"]))
        status, saved = call(handler, patch(created, changed))
        assert status == 200
        assert saved["operationState"] == "SUCCEEDED"
        stub.assert_no_pending_responses()


@mock_aws
def test_update_still_processing_in_aws_is_not_judged_as_unapplied():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        stub.add_response("get_registry_record", record())
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        _, pending = call(handler, patch(created, {**DRAFT, "description": "処理中"}))
        stub.add_response("get_registry_record", record(status="UPDATING"))
        status, body = call(handler, event("GET", f"/api/operations/{pending['operationId']}"))
        assert status == 202
        assert body["operationState"] == "VERIFYING"
        stub.assert_no_pending_responses()


@mock_aws
def test_double_click_that_races_past_the_first_lookup_creates_only_one_record():
    registry, db = clients()
    handler = handler_for(registry, db)
    creates = []
    racing = [True]

    def count_create(params, **kwargs):
        creates.append(params["clientToken"])

    def second_click_wins_the_lock(params, **kwargs):
        # 1回目の begin より先に、2回目のクリックが操作を最後まで終える
        if racing[0] and any(
            item.get("Put", {}).get("Item", {}).get("sk") == {"S": "LOCK"}
            and item["Put"]["Item"]["pk"]["S"].endswith(f"#NAME#{DRAFT['name']}")
            for item in params["TransactItems"]
        ):
            racing[0] = False
            nested.append(call(handler, event(body=DRAFT)))

    nested = []
    registry.meta.events.register(
        "before-parameter-build.agent-registry-control.CreateRegistryRecord", count_create
    )
    db.meta.events.register(
        "before-parameter-build.dynamodb.TransactWriteItems", second_click_wins_the_lock
    )
    with Stubber(registry) as stub:
        stub_create(stub)
        status, first = call(handler, event(body=DRAFT))
        assert nested[0][0] == 201
        assert (status, first) == nested[0]
        assert len(creates) == 1
        stub.assert_no_pending_responses()


@mock_aws
def test_update_stopped_before_writing_releases_the_record_lock():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        for _ in range(2):
            stub.add_response("get_registry_record", record())
            status, body = call(handler, patch(created, {**DRAFT, "name": "renamed-agent"}))
            # 2回目もロック待ち（409）ではなく、同じ入力エラーになる
            assert status == 400
            assert body["error"]["code"] == "VALIDATION_ERROR"
        stub.assert_no_pending_responses()


@mock_aws
def test_saving_a_draft_does_not_require_a_version_greater_than_the_published_one():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        changed = {**DRAFT, "description": "版は申請時に検査する"}
        stub.add_response("get_registry_record", record(status="APPROVED"))
        stub.add_response("update_registry_record", record(status="UPDATING"))
        stub.add_response("get_registry_record", record(description=changed["description"]))
        status, saved = call(handler, patch(created, changed))
        assert status == 200
        assert saved["publishedVersion"] == "1.0.0"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "request_event",
    [
        event(body=DRAFT),
        event("GET", "/api/records"),
        event("GET", f"/api/records/{RECORD_ID}"),
        event(
            "PATCH", f"/api/records/{RECORD_ID}", {"draft": DRAFT, "expectedContentHash": "a" * 64}
        ),
        event("GET", f"/api/operations/{'a' * 64}"),
    ],
)
def test_unconfigured_supplement_table_is_503_on_every_draft_route(request_event):
    """補助テーブルの未設定は入力誤り（400）や障害（502）ではなく、準備中として揃えて返す。"""
    status, body = call(create_management_handler(ENV), request_event)
    assert status == 503
    assert body["error"]["code"] == "DRAFTS_NOT_CONFIGURED"


def test_unconfigured_supplement_table_still_rejects_a_viewer_first():
    status, body = call(create_management_handler(ENV), event("GET", "/api/records", groups=""))
    assert (status, body["error"]["code"]) == (403, "FORBIDDEN")


@mock_aws
def test_settled_operation_expires_by_ttl_but_lock_and_pending_operation_do_not():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_response("create_registry_record", {"recordArn": RECORD_ARN, "status": "CREATING"})
        stub.add_response("get_registry_record", record(status="CREATING"))
        handler = handler_for(registry, db)
        _, pending = call(handler, event(body=DRAFT))
        control = items_with_sk(db, "CONTROL")
        assert len(control) == 1 and "expiresAt" not in control[0]
        assert all("expiresAt" not in lock for lock in items_with_sk(db, "LOCK"))
        stub.add_response("get_registry_record", record())
        before = int(datetime.now(timezone.utc).timestamp())
        assert call(handler, event("GET", f"/api/operations/{pending['operationId']}"))[0] == 200
        stub.assert_no_pending_responses()
    (settled,) = items_with_sk(db, "CONTROL")
    expires = int(settled["expiresAt"]["N"])
    assert before + 7 * 24 * 3600 <= expires <= before + 7 * 24 * 3600 + 60
    assert items_with_sk(db, "LOCK") == []
    # 操作記録（追記記録）は無期限に保持する
    assert all("expiresAt" not in item for item in items_with_sk(db, "AUDIT#"))


@mock_aws
def test_management_keeps_the_last_editor_and_audit_keeps_the_aws_request_id():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub.add_response(
            "create_registry_record",
            {
                "recordArn": RECORD_ARN,
                "status": "CREATING",
                "ResponseMetadata": {"RequestId": "aws-create-1"},
            },
        )
        stub.add_response("get_registry_record", record())
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=DRAFT))
        assert management_item(db)["lastEditedBy"] == {
            "M": {"issuer": {"S": ISSUER}, "subject": {"S": "alice"}}
        }
        changed = {**DRAFT, "description": "別のメンバーが編集する"}
        stub.add_response("get_registry_record", record())
        stub.add_response(
            "update_registry_record",
            {**record(status="UPDATING"), "ResponseMetadata": {"RequestId": "aws-update-1"}},
        )
        stub.add_response("get_registry_record", record(description=changed["description"]))
        request = patch(created, changed)
        request["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "bob"
        assert call(handler, request)[0] == 200
        stub.assert_no_pending_responses()
    saved = management_item(db)
    assert saved["lastEditedBy"]["M"]["subject"] == {"S": "bob"}
    assert "lastEditedAt" in saved
    assert sorted(item["awsRequestId"]["S"] for item in audit_items_ending_with(db, "#RESULT")) == [
        # 作成の操作記録は名前ロックとレコードの両方に追記される（#11 の履歴のため）
        "aws-create-1",
        "aws-create-1",
        "aws-update-1",
    ]
