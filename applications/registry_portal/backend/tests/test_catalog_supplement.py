"""補助テーブルを含む詳細 API の公開版選択と異常時の応答を検証する（Seam 1）。"""

from typing import Any

import pytest
from botocore.stub import Stubber
from moto import mock_aws

from catalog_helpers import (
    REGISTRY_ARN,
    SUPPLEMENT_TABLE,
    FakeTransport,
    call,
    detail_event,
    dynamodb_client,
    found,
    hit,
    make_handler,
)

RECORD_ID = "agentAbc1234"
PARTITION_KEY = f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"


def detail_response(client: Any) -> tuple[int, dict[str, Any]]:
    transport = FakeTransport()
    transport.registry.append(found(hit(RECORD_ID, customMetadata={"ownerTeam": "team-legacy"})))
    handler = make_handler(
        transport, env={"SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE}, supplement_client=client
    )
    return call(handler, detail_event(RECORD_ID))


def snapshot(reference: str, terms: str) -> dict[str, Any]:
    return {
        "termsOfUse": {"S": terms},
        "relatedReferences": {
            "L": [{"M": {"recordId": {"S": reference}, "recordVersion": {"S": "3.0.0"}}}]
        },
    }


@mock_aws
def test_detail_reads_only_the_discoverable_version_from_persisted_snapshots() -> None:
    client = dynamodb_client()
    client.create_table(
        TableName=SUPPLEMENT_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.put_item(
        TableName=SUPPLEMENT_TABLE,
        Item={
            "pk": {"S": PARTITION_KEY},
            "sk": {"S": "MANAGEMENT"},
            "managed": {"BOOL": True},
            "ownerTeam": {"S": "team-current"},
        },
    )
    for key, reference, terms in [
        ("PUBLISHED#0.9.0", "oldSkill1234", "旧版の条件"),
        ("PUBLISHED#1.0.0", "publicSkill1", "公開中の条件"),
        ("DRAFT#2.0.0", "draftSkill12", "下書きの条件"),
    ]:
        client.put_item(
            TableName=SUPPLEMENT_TABLE,
            Item={"pk": {"S": PARTITION_KEY}, "sk": {"S": key}, **snapshot(reference, terms)},
        )

    status, body = detail_response(client)

    assert status == 200
    assert body["ownerTeam"] == "team-current"
    assert body["termsOfUse"] == "公開中の条件"
    assert body["relatedReferences"] == [
        {"recordId": "publicSkill1", "recordVersion": "3.0.0"},
    ]


@pytest.mark.parametrize(
    ("management", "published"),
    [
        (None, None),
        ({"Item": {"managed": {"BOOL": True}}}, None),
        ({"Item": {"ownerTeam": {"S": "team-current"}}}, {"Item": {"contact": {"N": "1"}}}),
        ({"Item": {"ownerTeam": {"S": "team-current"}}}, {"Item": {"termsOfUse": {"S": "条件"}}}),
        (
            {"Item": {"ownerTeam": {"S": "team-current"}}},
            {"Item": {"relatedReferences": {"L": [{"M": {"recordId": {"S": "skill"}}}]}}},
        ),
    ],
    ids=[
        "access-denied",
        "missing-owner-team",
        "contact-not-string",
        "agent-without-related-references",
        "reference-without-version",
    ],
)
def test_detail_fails_without_metadata_fallback_when_the_supplement_breaks_its_contract(
    management: dict[str, Any] | None, published: dict[str, Any] | None
) -> None:
    client = dynamodb_client()
    with Stubber(client) as stubber:
        if management is None:
            stubber.add_client_error("get_item", service_error_code="AccessDeniedException")
        else:
            stubber.add_response("get_item", management)
        if published is not None:
            stubber.add_response("get_item", published)
        status, body = detail_response(client)
        stubber.assert_no_pending_responses()

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
    assert "ownerTeam" not in body
