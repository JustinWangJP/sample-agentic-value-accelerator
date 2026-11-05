"""検索と詳細を API Gateway HTTP API v2 のイベントとフェイク HTTP で検証する（Seam 1）。"""

import json
from typing import Any

import pytest
from botocore.stub import Stubber

from registry_portal.config import ConfigurationError
from registry_portal.services.http import TransportError, TransportResponse

from catalog_helpers import (
    BATCH_URL,
    ENV,
    REGISTRY_ARN,
    SEARCH_URL,
    SUPPLEMENT_TABLE,
    FakeTransport,
    botocore_discovery_request,
    call,
    detail_event,
    dynamodb_client,
    found,
    hit,
    json_response,
    make_handler,
    search_event,
    summary,
    supplement_key,
)

SKILL_ID = "skillAbc1234"
AGENT_ID = "agentAbc1234"
SKILL_MD = "# 手順\n<script>alert('test')</script>\n<img src=x onerror=alert(1)>"
A2A_READY = {"A2A_SEARCH_ENABLED": "true"}
WITH_SUPPLEMENT = {"SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE}


def test_viewer_searches_natural_language_with_only_allowlisted_filters() -> None:
    transport = FakeTransport()
    transport.registry.append(json_response(200, {"registryRecords": [hit("z"), hit("a")]}))

    status, body = call(
        make_handler(transport, env=A2A_READY),
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
    assert body["inconsistentA2ACount"] == 0
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
            "$and": [
                {"recordType": {"$eq": "AGENT"}},
                {"customMetadata.isA2A": {"$eq": True}},
                {"customMetadata.dataClassification": {"$eq": "private"}},
            ],
        },
    )
    assert request.method == expected["method"]
    assert request.url == expected["url"]
    assert request.json() == json.loads(expected["body"])


def test_keyword_search_preserves_ranking_and_returns_at_most_twenty() -> None:
    transport = FakeTransport()
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


def test_data_classification_filter_is_applied_by_registry_with_the_query() -> None:
    transport = FakeTransport()
    transport.registry.append(json_response(200, {"registryRecords": [hit("b", "mcpServer")]}))

    status, body = call(
        make_handler(transport),
        search_event({"query": "在庫", "type": "MCP", "dataClassification": "public"}),
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["b"]
    (request,) = transport.to(SEARCH_URL)
    assert request.json()["searchQuery"] == "在庫"
    assert request.json()["filters"] == {
        "$and": [
            {"recordType": {"$eq": "MCP"}},
            {"customMetadata.dataClassification": {"$eq": "public"}},
        ],
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
        {"query": ""},
        {"query": None, "type": "AGENT"},
        # AWS の searchQuery は必須。条件だけで探す場合は一覧を使う
        {"type": "AGENT", "dataClassification": "private"},
    ],
)
def test_search_rejects_invalid_or_unapproved_fields_before_external_requests(payload: Any) -> None:
    transport = FakeTransport()

    status, body = call(make_handler(transport, env=A2A_READY), search_event(payload))

    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert transport.requests == []


@pytest.mark.parametrize("a2a", [True, False])
def test_a2a_search_is_refused_until_the_search_metadata_is_ready(a2a: bool) -> None:
    transport = FakeTransport()

    status, body = call(make_handler(transport), search_event({"query": "受注", "a2a": a2a}))

    assert status == 503
    assert body["error"]["code"] == "A2A_SEARCH_NOT_READY"
    assert "A2A の Agent のみ" in body["error"]["message"]
    assert transport.requests == []


@pytest.mark.parametrize("value", ["yes", "TRUE", "1"])
def test_malformed_a2a_search_setting_stops_startup(value: str) -> None:
    with pytest.raises(ConfigurationError, match="A2A_SEARCH_ENABLED"):
        make_handler(FakeTransport(), env={"A2A_SEARCH_ENABLED": value})


def test_no_search_match_is_a_successful_empty_response() -> None:
    transport = FakeTransport()
    transport.registry.append(json_response(200, {"registryRecords": []}))

    assert call(make_handler(transport), search_event({"query": "存在しない"})) == (
        200,
        {"items": [], "inconsistentA2ACount": 0},
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
    transport = FakeTransport()
    transport.registry.append(response)

    status, body = call(make_handler(transport), search_event({"query": "注文"}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"


def test_search_requires_a_viewer_before_calling_registry() -> None:
    transport = FakeTransport()
    event = search_event({"query": "受注"})
    del event["requestContext"]["authorizer"]

    status, body = call(make_handler(transport), event)

    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert transport.requests == []


def test_search_fails_rather_than_exposing_an_unapproved_record() -> None:
    transport = FakeTransport()
    draft = {**hit(AGENT_ID), "status": "DRAFT"}
    transport.registry.append(json_response(200, {"registryRecords": [draft]}))

    status, body = call(make_handler(transport), search_event({"query": "agent"}))

    assert status == 502
    assert "items" not in body


def published_skill(**metadata: str) -> dict[str, Any]:
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
        customMetadata=metadata,
    )
    del skill["descriptorTypes"]
    return skill


def test_viewer_reads_the_published_skill_and_its_details_with_a_single_batch_lookup() -> None:
    transport = FakeTransport()
    transport.registry.append(
        found(
            published_skill(
                ownerTeam="team-orders",
                dataClassification="private",
                contact="custom metadata の値は表示しない",
                unrelated="利用者には不要な値",
            )
        )
    )
    dynamodb = dynamodb_client()
    with Stubber(dynamodb) as stubber:
        stubber.add_response(
            "get_item",
            {"Item": {"managed": {"BOOL": True}, "ownerTeam": {"S": "team-orders"}}},
            expected_params=supplement_key(SKILL_ID, "MANAGEMENT"),
        )
        stubber.add_response(
            "get_item",
            {
                "Item": {
                    "contact": {"S": "orders@example.com"},
                    "termsOfUse": {"S": "開発環境のみ"},
                    "connectionReference": {"S": "packages/orders@1.2.3"},
                }
            },
            expected_params=supplement_key(SKILL_ID, "PUBLISHED#1.2.3"),
        )
        handler = make_handler(transport, env=WITH_SUPPLEMENT, supplement_client=dynamodb)
        status, body = call(handler, detail_event(SKILL_ID))
        stubber.assert_no_pending_responses()

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
    assert "custom metadata の値" not in json.dumps(body, ensure_ascii=False)
    (request,) = transport.to(BATCH_URL)
    expected = botocore_discovery_request(
        "batch_get_discoverable_registry_record",
        entries=[{"registryId": REGISTRY_ARN, "recordIds": [SKILL_ID]}],
    )
    assert request.method == expected["method"]
    assert request.url == expected["url"]
    assert request.json() == json.loads(expected["body"])


def test_related_references_and_terms_are_read_for_the_published_agent_version() -> None:
    transport = FakeTransport()
    transport.registry.append(found(hit(AGENT_ID)))
    dynamodb = dynamodb_client()
    with Stubber(dynamodb) as stubber:
        stubber.add_response(
            "get_item",
            {"Item": {"ownerTeam": {"S": "team-agents"}}},
            expected_params=supplement_key(AGENT_ID, "MANAGEMENT"),
        )
        stubber.add_response(
            "get_item",
            {
                "Item": {
                    "termsOfUse": {"S": "社内の検証用途のみ"},
                    "relatedReferences": {
                        "L": [
                            {
                                "M": {
                                    "recordId": {"S": SKILL_ID},
                                    "recordVersion": {"S": "2.3.4"},
                                }
                            },
                        ]
                    },
                }
            },
            expected_params=supplement_key(AGENT_ID, "PUBLISHED#1.0.0"),
        )
        handler = make_handler(transport, env=WITH_SUPPLEMENT, supplement_client=dynamodb)
        status, body = call(handler, detail_event(AGENT_ID))
        stubber.assert_no_pending_responses()

    assert status == 200
    assert body["ownerTeam"] == "team-agents"
    assert body["termsOfUse"] == "社内の検証用途のみ"
    # スナップショットに値がない項目は空文字（画面では「未登録」）
    assert body["contact"] == ""
    assert body["connectionReference"] == ""
    assert body["relatedReferences"] == [{"recordId": SKILL_ID, "recordVersion": "2.3.4"}]


@pytest.mark.parametrize(
    ("management", "owner_team"),
    [
        ({"Item": {"ownerTeam": {"S": "team-current"}}}, "team-current"),
        ({"Item": {"managed": {"BOOL": False}, "ownerTeam": {"S": "team-old"}}}, "team-legacy"),
        ({}, "team-legacy"),
    ],
    ids=["managed", "unmanaged-flag", "unmanaged-missing"],
)
def test_detail_uses_authoritative_owner_team_or_falls_back_for_unmanaged_records(
    management: dict[str, Any], owner_team: str
) -> None:
    transport = FakeTransport()
    transport.registry.append(found(published_skill(ownerTeam="team-legacy")))
    dynamodb = dynamodb_client()
    with Stubber(dynamodb) as stubber:
        stubber.add_response(
            "get_item", management, expected_params=supplement_key(SKILL_ID, "MANAGEMENT")
        )
        stubber.add_response(
            "get_item", {}, expected_params=supplement_key(SKILL_ID, "PUBLISHED#1.2.3")
        )
        handler = make_handler(transport, env=WITH_SUPPLEMENT, supplement_client=dynamodb)
        status, body = call(handler, detail_event(SKILL_ID))
        stubber.assert_no_pending_responses()

    assert status == 200
    assert body["ownerTeam"] == owner_team
    # 公開版スナップショットがない（未管理レコードなど）ときは null（画面では「未取得」）
    assert body["contact"] is None
    assert body["termsOfUse"] is None


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
    transport = FakeTransport()
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

    status, body = call(make_handler(transport), detail_event(SKILL_ID))

    assert status == http_status
    assert body["error"]["code"] == portal_code
    assert "内部メッセージ" not in body["error"]["message"]


def test_unconfigured_supplement_reports_snapshot_values_as_unknown() -> None:
    transport = FakeTransport()
    transport.registry.append(found(hit(AGENT_ID, customMetadata={"dataClassification": "top"})))

    status, body = call(make_handler(transport, env=ENV), detail_event(AGENT_ID))

    assert status == 200
    assert body["relatedReferences"] is None
    assert body["contact"] is None
    assert body["connectionReference"] is None
    # 許可していない値のデータ分類は表示しない
    assert body["dataClassification"] is None


@pytest.mark.parametrize(
    "path", ["/api/catalog/agentAbc1234/versions", "/api/catalog/", "/api/catalogs/agentAbc1234"]
)
def test_unknown_catalog_paths_are_not_routed_to_the_detail(path: str) -> None:
    transport = FakeTransport()
    event = detail_event(AGENT_ID)
    event["requestContext"]["http"]["path"] = path

    status, body = call(make_handler(transport), event)

    assert status == 404
    assert body["error"]["code"] == "NOT_FOUND"
    assert transport.requests == []
