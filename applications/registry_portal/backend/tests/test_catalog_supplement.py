"""補助テーブルを含む詳細 API の公開版選択と異常時の応答を検証する。"""

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from registry_portal.app import create_management_handler
from registry_portal.services.discovery import DiscoveryDependencies
from test_catalog_discovery import SearchTransport, detail_event, hit
from test_catalog_list import ENV, REGISTRY_ARN, FakeSecrets, call, json_response


RECORD_ID = "agentAbc1234"
TABLE_NAME = "registry-auxiliary"
PARTITION_KEY = f"REGISTRY#{REGISTRY_ARN}#RECORD#{RECORD_ID}"


def detail_response(client):
    transport = SearchTransport()
    record = hit(RECORD_ID)
    record["customMetadata"] = {"ownerTeam": "team-legacy"}
    transport.registry.append(json_response(200, {"registryRecords": [record], "errors": []}))
    handler = create_management_handler(
        {**ENV, "CATALOG_REFERENCES_TABLE_NAME": TABLE_NAME},
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
        supplement_client=client,
    )
    return call(handler, detail_event(RECORD_ID))


def dynamodb_client():
    return boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


@mock_aws
def test_detail_reads_only_the_discoverable_version_from_persisted_snapshots():
    client = dynamodb_client()
    client.create_table(
        TableName=TABLE_NAME,
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
        TableName=TABLE_NAME,
        Item={
            "pk": {"S": PARTITION_KEY},
            "sk": {"S": "MANAGEMENT"},
            "managed": {"BOOL": True},
            "ownerTeam": {"S": "team-current"},
        },
    )
    for key, reference in [
        ("PUBLISHED#0.9.0", "oldSkill1234"),
        ("PUBLISHED#1.0.0", "publicSkill1"),
        ("DRAFT#2.0.0", "draftSkill12"),
    ]:
        client.put_item(
            TableName=TABLE_NAME,
            Item={
                "pk": {"S": PARTITION_KEY},
                "sk": {"S": key},
                "relatedReferences": {
                    "L": [
                        {
                            "M": {
                                "recordId": {"S": reference},
                                "recordVersion": {"S": "3.0.0"},
                            }
                        }
                    ]
                },
            },
        )

    status, body = detail_response(client)

    assert status == 200
    assert body["ownerTeam"] == "team-current"
    assert body["relatedReferences"] == [
        {"recordId": "publicSkill1", "recordVersion": "3.0.0"},
    ]


@pytest.mark.parametrize("failure", ["access_denied", "missing_owner"])
def test_detail_fails_without_metadata_fallback_when_management_cannot_be_read(failure):
    client = dynamodb_client()
    with Stubber(client) as stubber:
        if failure == "access_denied":
            stubber.add_client_error("get_item", service_error_code="AccessDeniedException")
        else:
            stubber.add_response("get_item", {"Item": {"managed": {"BOOL": True}}})
        status, body = detail_response(client)
        stubber.assert_no_pending_responses()

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
    assert "ownerTeam" not in body
