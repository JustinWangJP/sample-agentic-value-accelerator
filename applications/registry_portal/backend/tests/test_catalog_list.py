"""カタログ一覧（GET /api/catalog）を、発見アダプターのトランスポートをフェイクにして検証する（Seam 1）。"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs

import pytest

from registry_portal.services.http import TransportError, TransportResponse

from catalog_helpers import (
    CLIENT_SECRET,
    LIST_URL,
    REGISTRY_ARN,
    SECRET_ARN,
    TOKEN_URL,
    Clock,
    FakeSecrets,
    FakeTransport,
    access_claims,
    basic_credentials,
    botocore_discovery_request,
    call,
    catalog_event,
    json_response,
    make_handler,
    page,
    summary,
)


def test_viewer_lists_first_page_of_agents_with_m2m_bearer_token() -> None:
    transport = FakeTransport()
    transport.registry.append(
        json_response(
            200,
            {
                "registryRecords": [
                    summary("rec-a2a", descriptorTypes=["a2aAgentCard"]),
                    summary("rec-custom"),
                ],
                "nextToken": "page-2-token",
            },
        )
    )

    status, body = call(make_handler(transport), catalog_event({"type": "AGENT"}))

    assert status == 200
    assert body == {
        "items": [
            {
                "recordId": "rec-a2a",
                "name": "name-rec-a2a",
                "displayName": "表示名 rec-a2a",
                "description": "説明 rec-a2a",
                "recordType": "AGENT",
                "descriptorTypes": ["a2aAgentCard"],
                "isA2A": True,
                "recordVersion": "1.0.0",
                "updatedAt": "2026-11-02T00:00:00Z",
            },
            {
                "recordId": "rec-custom",
                "name": "name-rec-custom",
                "displayName": "表示名 rec-custom",
                "description": "説明 rec-custom",
                "recordType": "AGENT",
                "descriptorTypes": ["custom"],
                "isA2A": False,
                "recordVersion": "1.0.0",
                "updatedAt": "2026-11-02T00:00:00Z",
            },
        ],
        "nextToken": "page-2-token",
    }
    [list_request] = transport.to(LIST_URL)
    assert list_request.method == "POST"
    assert list_request.headers["authorization"] == "Bearer m2m-token-1"
    assert list_request.json() == {
        "filters": [{"name": "recordType", "values": ["AGENT"]}],
        "maxResults": 50,
    }
    # client credentials で M2M トークンを取得する。secret は Secrets Manager から得る
    [token_request] = transport.to(TOKEN_URL)
    assert parse_qs(token_request.body.decode() if token_request.body else "") == {
        "grant_type": ["client_credentials"],
        "scope": ["registry/discovery.read"],
    }
    assert basic_credentials(token_request) == f"discovery-m2m-client:{CLIENT_SECRET}"


def test_next_token_from_viewer_is_relayed_to_registry_and_last_page_has_none() -> None:
    transport = FakeTransport()
    transport.registry.append(page(summary("rec-z", "MCP", descriptorTypes=["mcpServer"])))

    status, body = call(
        make_handler(transport), catalog_event({"type": "MCP", "nextToken": "page-2-token"})
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["rec-z"]
    assert body["nextToken"] is None
    [list_request] = transport.to(LIST_URL)
    assert list_request.json() == {
        "filters": [{"name": "recordType", "values": ["MCP"]}],
        "maxResults": 50,
        "nextToken": "page-2-token",
    }


def test_a2a_agent_list_filters_at_registry_and_relays_its_page_tokens() -> None:
    transport = FakeTransport()
    transport.registry.append(
        page(summary("rec-a2a", descriptorTypes=["a2aAgentCard"]), next_token="a2a-page-3")
    )

    status, body = call(
        make_handler(transport),
        catalog_event({"type": "AGENT", "a2a": "true", "nextToken": "a2a-page-2"}),
    )

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["rec-a2a"]
    assert body["nextToken"] == "a2a-page-3"
    [request] = transport.to(LIST_URL)
    expected = botocore_discovery_request(
        "list_discoverable_registry_records",
        registryId=REGISTRY_ARN,
        filters=[
            {"name": "recordType", "values": ["AGENT"]},
            {"name": "descriptorType", "values": ["a2aAgentCard"]},
        ],
        maxResults=50,
        nextToken="a2a-page-2",
    )
    assert request.method == expected["method"]
    assert request.url == expected["url"]
    assert request.json() == json.loads(expected["body"])


def test_empty_page_with_next_token_is_returned_as_is() -> None:
    # AWS はページが疎であることを保証しない。0件でも nextToken があれば続きがある
    transport = FakeTransport()
    transport.registry.append(page(next_token="page-3-token"))

    status, body = call(make_handler(transport), catalog_event({"type": "SKILL"}))

    assert status == 200
    assert body == {"items": [], "nextToken": "page-3-token"}


def test_m2m_token_is_reused_until_shortly_before_expiry() -> None:
    transport = FakeTransport()
    clock = Clock()
    secrets = FakeSecrets()
    handler = make_handler(transport, clock, secrets)
    transport.registry.extend([page(), page(), page()])

    call(handler, catalog_event({"type": "AGENT"}))
    clock.now += 3600 - 61  # 有効期限の60秒前より前は再利用する
    call(handler, catalog_event({"type": "AGENT"}))
    clock.now += 1  # 有効期限の60秒前を過ぎたら取り直す
    call(handler, catalog_event({"type": "AGENT"}))

    bearer = [r.headers["authorization"] for r in transport.to(LIST_URL)]
    assert bearer == ["Bearer m2m-token-1", "Bearer m2m-token-1", "Bearer m2m-token-2"]
    assert len(transport.to(TOKEN_URL)) == 2
    # secret はメモリー上で再利用する（GetSecretValue は1回だけ）
    secrets.stubber.assert_no_pending_responses()


def test_registry_401_refreshes_token_once_and_succeeds() -> None:
    transport = FakeTransport()
    transport.registry.extend([json_response(401, {"message": "Unauthorized"}), page(summary("a"))])

    status, body = call(make_handler(transport), catalog_event({"type": "AGENT"}))

    assert status == 200
    assert [item["recordId"] for item in body["items"]] == ["a"]
    bearer = [r.headers["authorization"] for r in transport.to(LIST_URL)]
    assert bearer == ["Bearer m2m-token-1", "Bearer m2m-token-2"]


def test_repeated_registry_401_stops_after_one_refresh_and_reports_failure() -> None:
    transport = FakeTransport()
    transport.registry.extend([json_response(401, {"message": "Unauthorized"})] * 2)

    status, body = call(make_handler(transport), catalog_event({"type": "AGENT"}))

    assert status == 502
    assert body["error"] == {
        "code": "DISCOVERY_UNAVAILABLE",
        "message": "カタログを取得できませんでした。時間をおいて再度お試しください。",
        "requestId": "req-cat",
    }
    # 再取得は1回に限る（Registry へ2回、トークン取得も2回）
    assert len(transport.to(LIST_URL)) == 2
    assert len(transport.to(TOKEN_URL)) == 2


@pytest.mark.parametrize(
    "query",
    [
        # Registry ID や endpoint URL を利用者から受け取らない
        {"type": "AGENT", "registryId": "otherRegistry0001"},
        {"type": "AGENT", "endpoint": "https://attacker.example.com"},
        {"type": "AGENT", "maxResults": "100"},
        {"type": "AGENT", "descriptorType": "a2aAgentCard"},
        {"type": "AGENT", "a2a": "false"},
        {"type": "AGENT", "a2a": "true,true"},
        {"type": "MCP", "a2a": "true"},
    ],
    ids=[
        "registry-id",
        "endpoint",
        "max-results",
        "descriptor",
        "a2a-false",
        "a2a-repeated",
        "a2a-mcp",
    ],
)
def test_unapproved_catalog_list_parameters_are_rejected(
    query: dict[str, str],
) -> None:
    transport = FakeTransport()

    status, body = call(make_handler(transport), catalog_event(query))

    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert transport.requests == []


@pytest.mark.parametrize(
    "query",
    [None, {}, {"type": "CUSTOM"}, {"type": "agent"}, {"type": "AGENT,MCP"}],
    ids=["no-query", "empty", "custom", "lowercase", "multiple"],
)
def test_type_must_be_one_of_agent_mcp_skill(query: dict[str, str] | None) -> None:
    transport = FakeTransport()

    status, body = call(make_handler(transport), catalog_event(query))

    assert status == 400
    assert body["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "種別（type）には AGENT、MCP、SKILL のいずれかを指定してください。",
        "requestId": "req-cat",
    }
    assert transport.requests == []


@pytest.mark.parametrize(
    "token",
    ["", "x" * 4097, "has space", "改行\n", "日本語"],
    ids=["empty", "too-long", "space", "control", "non-ascii"],
)
def test_malformed_next_token_is_rejected_before_calling_registry(token: str) -> None:
    transport = FakeTransport()

    status, body = call(
        make_handler(transport), catalog_event({"type": "AGENT", "nextToken": token})
    )

    assert status == 400
    assert body["error"]["code"] == "INVALID_PAGE_TOKEN"
    assert transport.requests == []


def test_next_token_of_maximum_length_is_accepted() -> None:
    transport = FakeTransport()
    transport.registry.append(page())
    token = "A" * 4096

    status, _ = call(make_handler(transport), catalog_event({"type": "AGENT", "nextToken": token}))

    assert status == 200
    assert transport.to(LIST_URL)[0].json()["nextToken"] == token


def test_next_token_rejected_by_registry_asks_viewer_to_start_over() -> None:
    transport = FakeTransport()
    # AWS の ValidationException（rest-json）。fieldList に nextToken があれば token の誤り
    transport.registry.append(
        json_response(
            400,
            {
                "message": "Invalid request",
                "reason": "FieldValidationFailed",
                "fieldList": [{"name": "nextToken", "message": "Invalid pagination token"}],
            },
        )
    )

    status, body = call(
        make_handler(transport), catalog_event({"type": "AGENT", "nextToken": "stale-token"})
    )

    assert status == 400
    assert body["error"]["code"] == "INVALID_PAGE_TOKEN"
    assert body["error"]["message"] == (
        "ページの指定が正しくありません。最初のページから表示し直してください。"
    )


@pytest.mark.parametrize(
    "failure",
    [
        json_response(500, {"message": "InternalServerException"}),
        json_response(429, {"message": "ThrottlingException"}),
        json_response(403, {"message": "AccessDeniedException"}),
        TransportResponse(status=200, body=b"<html>not json</html>"),
        json_response(200, {"registryRecords": [{"recordId": "no-name"}]}),
        TransportError("TimeoutError"),
    ],
    ids=["5xx", "throttled", "access-denied", "not-json", "malformed-record", "timeout"],
)
def test_registry_failures_are_distinguished_from_empty_result(
    failure: TransportResponse | TransportError,
) -> None:
    transport = FakeTransport()
    transport.registry.append(failure)

    status, body = call(make_handler(transport), catalog_event({"type": "AGENT"}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
    assert "items" not in body


@pytest.mark.parametrize(
    "failure",
    [
        json_response(400, {"error": "invalid_client"}),
        json_response(200, {"access_token": "t"}),  # expires_in がない
        TransportError("URLError"),
    ],
    ids=["invalid-client", "no-expiry", "unreachable"],
)
def test_token_endpoint_failure_is_reported_without_calling_registry(
    failure: TransportResponse | TransportError,
) -> None:
    transport = FakeTransport()
    transport.tokens.append(failure)

    status, body = call(make_handler(transport), catalog_event({"type": "AGENT"}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
    assert transport.to(LIST_URL) == []


def test_discovery_m2m_token_cannot_call_catalog_api() -> None:
    # 発見用 M2M client のトークンはポータル API では受け付けない
    transport = FakeTransport()
    claims = {**access_claims(), "client_id": "discovery-m2m-client", "sub": "discovery-m2m-client"}

    status, _ = call(make_handler(transport), catalog_event({"type": "AGENT"}, claims=claims))

    assert status == 403
    assert transport.requests == []


def test_logs_never_contain_jwt_or_secret(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    transport = FakeTransport()
    transport.registry.extend(
        [
            json_response(401, {"message": "Unauthorized"}),
            json_response(401, {"message": "Unauthorized"}),
            page(summary("a")),
            json_response(500, {"message": "boom"}),
        ]
    )
    handler = make_handler(transport)

    call(handler, catalog_event({"type": "AGENT"}))  # 401 の再取得上限
    call(handler, catalog_event({"type": "AGENT"}))  # 成功
    call(handler, catalog_event({"type": "AGENT"}))  # 5xx

    assert caplog.records, "失敗の記録はログに残す"
    logged = caplog.text
    for secret in (CLIENT_SECRET, "m2m-token-", "Bearer", "Basic"):
        assert secret not in logged


def test_client_secret_stored_as_json_uses_client_secret_field() -> None:
    transport = FakeTransport()
    transport.registry.append(page())
    secrets = FakeSecrets(json.dumps({"client_secret": "json-secret"}))

    status, _ = call(make_handler(transport, secrets=secrets), catalog_event({"type": "AGENT"}))

    assert status == 200
    assert basic_credentials(transport.to(TOKEN_URL)[0]) == "discovery-m2m-client:json-secret"


def test_rejected_client_credentials_refetch_secret_on_next_request() -> None:
    # secret のローテーションに備え、トークンエンドポイントが拒否したら secret から取り直す
    transport = FakeTransport()
    transport.tokens.append(json_response(400, {"error": "invalid_client"}))
    transport.registry.append(page())
    secrets = FakeSecrets("old-secret", "rotated-secret")
    handler = make_handler(transport, secrets=secrets)

    first, _ = call(handler, catalog_event({"type": "AGENT"}))
    second, _ = call(handler, catalog_event({"type": "AGENT"}))

    assert (first, second) == (502, 200)
    secrets.stubber.assert_no_pending_responses()
    credentials = [basic_credentials(r) for r in transport.to(TOKEN_URL)]
    assert credentials == ["discovery-m2m-client:old-secret", "discovery-m2m-client:rotated-secret"]


@pytest.mark.parametrize("token", ["m2m-token\r\nx-injected: 1", "秘密の値", "has space"])
def test_malformed_access_token_is_not_sent_or_logged(
    token: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    transport = FakeTransport()
    transport.tokens.append(json_response(200, {"access_token": token, "expires_in": 3600}))

    status, body = call(make_handler(transport), catalog_event({"type": "AGENT"}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
    assert transport.to(LIST_URL) == []
    assert token not in caplog.text


@pytest.mark.parametrize("code", ["AccessDeniedException", "ResourceNotFoundException"])
def test_secret_retrieval_failure_is_reported_without_calling_token_endpoint(
    code: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    transport = FakeTransport()
    secrets = FakeSecrets(error_code=code)

    status, body = call(make_handler(transport, secrets=secrets), catalog_event({"type": "AGENT"}))

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"
    assert transport.requests == []
    # AWS のエラーメッセージ（ARN を含みうる）はログに出さず、エラーコードだけを残す
    assert code in caplog.text
    assert SECRET_ARN not in caplog.text


@pytest.mark.parametrize(
    "error_body",
    [
        # nextToken 以外の項目の誤り（ポータルの不具合）は、token の誤りと案内しない
        {
            "message": "Invalid request",
            "reason": "FieldValidationFailed",
            "fieldList": [{"name": "filters", "message": "Duplicate filter name"}],
        },
        # どの項目の誤りか分からない
        {"message": "Invalid request", "reason": "CannotParse"},
        {"message": "Invalid request", "reason": "FieldValidationFailed", "fieldList": "x"},
    ],
    ids=["other-field", "no-field-list", "malformed-field-list"],
)
def test_registry_400_not_about_next_token_is_reported_as_unavailable(
    error_body: dict[str, Any],
) -> None:
    transport = FakeTransport()
    transport.registry.append(json_response(400, error_body))

    status, body = call(
        make_handler(transport), catalog_event({"type": "AGENT", "nextToken": "page-2-token"})
    )

    assert status == 502
    assert body["error"]["code"] == "DISCOVERY_UNAVAILABLE"


@pytest.mark.parametrize(
    "query",
    [{"type": "SKILL"}, {"type": "MCP", "nextToken": "page-2/token+="}],
    ids=["first-page", "next-page"],
)
def test_list_request_matches_botocore_model_serialization(query: dict[str, str]) -> None:
    """手で組み立てた発見 API の URL と payload が、固定版 Botocore のモデルと一致する。

    JWT 認可の Registry は Boto3 では呼べないので HTTP で直接呼ぶ。その形が AWS の
    モデル（agent-registry の ListDiscoverableRegistryRecords）とずれないことを確かめる。
    """
    transport = FakeTransport()
    transport.registry.append(page())
    call(make_handler(transport), catalog_event(query))
    [sent] = transport.to(LIST_URL)

    expected = botocore_discovery_request(
        "list_discoverable_registry_records",
        registryId=REGISTRY_ARN,
        filters=[{"name": "recordType", "values": [query["type"]]}],
        maxResults=50,
        **({"nextToken": query["nextToken"]} if "nextToken" in query else {}),
    )

    assert sent.method == expected["method"]
    assert sent.url == expected["url"]
    assert sent.json() == json.loads(expected["body"])
