"""検索と詳細を API Gateway HTTP API v2 のイベントとフェイク HTTP で検証する。"""

import base64
import json
from collections.abc import Mapping
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from registry_portal.app import create_management_handler
from registry_portal.services.discovery import DiscoveryDependencies
from registry_portal.services.http import TransportError, TransportResponse

from test_catalog_list import (
    ENV,
    REGISTRY_ARN,
    FakeSecrets,
    FakeTransport,
    SentRequest,
    botocore_discovery_request,
    call,
    catalog_event,
    json_response,
    make_handler,
    summary,
)

SEARCH_URL = "https://agent-registry.us-east-1.api.aws/discoverable-records-search"
BATCH_URL = "https://agent-registry.us-east-1.api.aws/discoverable-records-batch"
SKILL_ID = "skillAbc1234"
SKILL_MD = "# 手順\n<script>alert('test')</script>\n<img src=x onerror=alert(1)>"


class SearchTransport(FakeTransport):
    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> TransportResponse:
        if url in (SEARCH_URL, BATCH_URL):
            self.requests.append(SentRequest(method, url, dict(headers), body))
            return self._next(self.registry)
        return super().__call__(method, url, dict(headers), body, timeout)


def search_event(payload: Any, *, encode: bool = False) -> dict[str, Any]:
    event = catalog_event()
    event["routeKey"] = "POST /api/catalog/search"
    event["requestContext"]["http"] = {"method": "POST", "path": "/api/catalog/search"}
    body = json.dumps(payload).encode()
    event["body"] = base64.b64encode(body).decode() if encode else body.decode()
    event["isBase64Encoded"] = encode
    return event


def hit(record_id: str, descriptor: str = "a2aAgentCard") -> dict[str, Any]:
    record = summary(record_id, descriptors={descriptor: {"data": "{}"}})
    del record["descriptorTypes"]
    return record


def test_viewer_searches_natural_language_with_only_allowlisted_filters() -> None:
    transport = SearchTransport()
    transport.registry.append(
        json_response(200, {"registryRecords": [hit("z"), hit("b", "http"), hit("a")]})
    )

    status, body = call(
        make_handler(transport),
        search_event(
            {
                "query": "受注を処理したい",
                "type": "AGENT",
                "a2a": True,
                "dataClassification": "private",
            },
            encode=True,
        ),
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["z", "a"]
    assert body["items"][0]["isA2A"] is True
    assert "nextToken" not in body
    (request,) = transport.to(SEARCH_URL)
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer m2m-token-1"
    expected = botocore_discovery_request(
        "search_discoverable_registry_records",
        searchQuery="受注を処理したい",
        registryIds=[REGISTRY_ARN],
        maxResults=20,
        filters={
            "recordType": {"$eq": "AGENT"},
            "customMetadata.dataClassification": {"$eq": "private"},
        },
    )
    assert request.method == expected["method"]
    assert request.url == expected["url"]
    assert request.json() == json.loads(expected["body"])


def test_keyword_search_preserves_ranking_and_returns_at_most_twenty() -> None:
    transport = SearchTransport()
    ranks = [hit(f"rank-{i:02d}") for i in range(24, 0, -1)]
    transport.registry.append(json_response(200, {"registryRecords": ranks}))
    status, body = call(make_handler(transport), search_event({"query": "order-agent"}))

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == [
        f"rank-{i:02d}" for i in range(24, 4, -1)
    ]
    assert transport.to(SEARCH_URL)[0].json()["filters"] == {
        "recordType": {"$in": ["AGENT", "MCP", "SKILL"]},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "x", "registryIds": ["elsewhere"]},
        {"query": "x", "filters": {"$or": []}},
        {"query": "x", "type": "CUSTOM"},
        {"query": "x", "type": "SKILL", "a2a": False},
        {"query": "x", "a2a": "true"},
        {"query": "x", "dataClassification": "confidential"},
        {"query": "x" * 257},
        {"query": "  "},
    ],
)
def test_search_rejects_invalid_or_unapproved_fields_before_external_requests(payload: Any) -> None:
    transport = SearchTransport()

    status, body = call(make_handler(transport), search_event(payload))

    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert transport.requests == []


def test_non_a2a_filter_keeps_only_agents_without_agent_cards() -> None:
    transport = SearchTransport()
    transport.registry.append(json_response(200, {"registryRecords": [hit("a"), hit("b", "http")]}))

    status, body = call(make_handler(transport), search_event({"query": "", "a2a": False}))

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["b"]
    assert transport.to(SEARCH_URL)[0].json()["filters"] == {"recordType": {"$eq": "AGENT"}}


def test_no_search_match_is_a_successful_empty_response() -> None:
    transport = SearchTransport()
    transport.registry.append(json_response(200, {"registryRecords": []}))

    assert call(make_handler(transport), search_event({"query": "存在しない"})) == (
        200,
        {"items": []},
    )


@pytest.mark.parametrize(
    "response",
    [
        TransportError("TimeoutError"),
        json_response(200, {}),
        json_response(403, {"message": "registry internal message"}),
    ],
)
def test_search_failure_is_not_an_empty_result(
    response: TransportResponse | TransportError,
) -> None:
    transport = SearchTransport()
    transport.registry.append(response)

    status, body = call(make_handler(transport), search_event({"query": "注文"}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"


def test_search_requires_a_viewer_before_calling_registry() -> None:
    transport = SearchTransport()
    event = search_event({"query": "受注"})
    del event["requestContext"]["authorizer"]

    status, body = call(make_handler(transport), event)

    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert transport.requests == []


def detail_event(record_id: str = SKILL_ID) -> dict[str, Any]:
    event = catalog_event()
    event["routeKey"] = "GET /api/catalog/{id}"
    event["requestContext"]["http"]["path"] = f"/api/catalog/{record_id}"
    event["pathParameters"] = {"id": record_id}
    return event


def test_viewer_reads_the_published_skill_and_its_details_with_a_single_batch_lookup() -> None:
    transport = SearchTransport()
    skill = summary(
        SKILL_ID,
        "SKILL",
        recordVersion="1.2.3",
        descriptors={
            "agentSkillsDefinition": {
                "data": '{"description":"注文を整理する"}',
                "dataSchemaVersion": "0.1.0",
                "additionalData": {"skillMd": {"data": SKILL_MD}},
            },
        },
        customMetadata={
            "ownerTeam": "team-orders",
            "contact": "orders@example.com",
            "termsOfUse": "開発環境のみ",
            "connectionReference": "packages/orders@1.2.3",
            "dataClassification": "private",
            "unrelated": "利用者には不要な値",
        },
    )
    del skill["descriptorTypes"]
    transport.registry.append(json_response(200, {"registryRecords": [skill], "errors": []}))

    status, body = call(make_handler(transport), detail_event())

    assert status == 200
    assert body["recordId"] == SKILL_ID
    assert body["recordVersion"] == "1.2.3"
    assert body["descriptorTypes"] == ["agentSkillsDefinition"]
    assert body["definition"]["agentSkillsDefinition"]["data"] == '{"description":"注文を整理する"}'
    assert body["ownerTeam"] == "team-orders"
    assert body["contact"] == "orders@example.com"
    assert body["termsOfUse"] == "開発環境のみ"
    assert body["connectionReference"] == "packages/orders@1.2.3"
    assert body["dataClassification"] == "private"
    assert body["skillMarkdown"] == SKILL_MD
    assert body["relatedReferences"] == []
    assert "unrelated" not in json.dumps(body)
    (request,) = transport.to(BATCH_URL)
    expected = botocore_discovery_request(
        "batch_get_discoverable_registry_record",
        entries=[{"registryId": REGISTRY_ARN, "recordIds": [SKILL_ID]}],
    )
    assert request.method == expected["method"]
    assert request.url == expected["url"]
    assert request.json() == json.loads(expected["body"])


def test_related_references_are_read_for_the_published_version() -> None:
    transport = SearchTransport()
    record = hit("agentAbc1234")
    transport.registry.append(json_response(200, {"registryRecords": [record], "errors": []}))
    dynamodb = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    with Stubber(dynamodb) as stubber:
        stubber.add_response(
            "get_item",
            {
                "Item": {
                    "relatedReferences": {
                        "L": [
                            {
                                "M": {
                                    "recordId": {"S": SKILL_ID},
                                    "recordVersion": {"S": "2.3.4"},
                                }
                            },
                        ]
                    }
                }
            },
            expected_params={
                "TableName": "registry-auxiliary",
                "ConsistentRead": True,
                "Key": {
                    "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#agentAbc1234"},
                    "sk": {"S": "PUBLISHED#1.0.0"},
                },
            },
        )
        handler = create_management_handler(
            {**ENV, "CATALOG_REFERENCES_TABLE_NAME": "registry-auxiliary"},
            discovery=DiscoveryDependencies(
                transport=transport,
                secrets_client=FakeSecrets().client,
            ),
            references_client=dynamodb,
        )
        status, body = call(handler, detail_event("agentAbc1234"))
        stubber.assert_no_pending_responses()

    assert status == 200
    assert body["relatedReferences"] == [{"recordId": SKILL_ID, "recordVersion": "2.3.4"}]


def test_search_never_exposes_a_non_public_record() -> None:
    transport = SearchTransport()
    draft = {**hit("agentAbc1234"), "status": "DRAFT"}
    transport.registry.append(json_response(200, {"registryRecords": [draft]}))

    status, body = call(make_handler(transport), search_event({"query": "agent"}))

    assert status == 502
    assert "items" not in body


@pytest.mark.parametrize(
    ("registry_code", "http_status", "portal_code"),
    [
        ("RESOURCE_NOT_FOUND", 404, "CATALOG_ITEM_NOT_FOUND"),
        ("ACCESS_DENIED", 502, "DISCOVERY_UNAVAILABLE"),
        ("INTERNAL_ERROR", 502, "DISCOVERY_UNAVAILABLE"),
    ],
)
def test_detail_distinguishes_a_missing_published_record_from_a_registry_failure(
    registry_code: str, http_status: int, portal_code: str
) -> None:
    transport = SearchTransport()
    transport.registry.append(
        json_response(
            200,
            {
                "registryRecords": [],
                "errors": [
                    {
                        "recordId": SKILL_ID,
                        "registryId": REGISTRY_ARN,
                        "errorCode": registry_code,
                        "message": "内部メッセージ",
                    }
                ],
            },
        )
    )

    status, body = call(make_handler(transport), detail_event())

    assert status == http_status
    assert body["error"]["code"] == portal_code
    assert "内部メッセージ" not in body["error"]["message"]


def test_unconfigured_related_references_are_reported_as_unknown() -> None:
    transport = SearchTransport()
    record = hit("agentAbc1234")
    transport.registry.append(json_response(200, {"registryRecords": [record], "errors": []}))

    status, body = call(make_handler(transport), detail_event("agentAbc1234"))

    assert status == 200
    assert body["relatedReferences"] is None
