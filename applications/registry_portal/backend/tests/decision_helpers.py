"""審査と決裁の Seam 1 テストで共有する Stubber の応答と操作の補助。"""

from __future__ import annotations

from collections import deque
from boto3.dynamodb.types import TypeSerializer
from catalog_helpers import (
    ENV,
    REGISTRY_ARN,
    SUPPLEMENT_TABLE,
    FakeSecrets,
    FakeTransport,
    json_response,
)
from draft_helpers import RECORD_ARN, RECORD_ID, aws_record, call, event
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
