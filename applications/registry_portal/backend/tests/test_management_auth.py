"""管理 handler の認可を、API Gateway HTTP API v2 のイベントで検証する（Seam 1）。"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

import pytest

from registry_portal import app as app_module
from registry_portal.app import create_management_handler
from registry_portal.config import ConfigurationError

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE"
HUMAN_CLIENT_ID = "human-spa-client"
DISCOVERY_CLIENT_ID = "discovery-m2m-client"

AUTH_ENV = {
    "COGNITO_ISSUER": ISSUER,
    "HUMAN_CLIENT_ID": HUMAN_CLIENT_ID,
}
DISCOVERY_ENV = {
    "REGISTRY_ARN": "arn:aws:agent-registry:us-east-1:123456789012:registry/abcdEFGH1234",
    "DISCOVERY_TOKEN_URL": "https://discovery.auth.us-east-1.amazoncognito.com/oauth2/token",
    "DISCOVERY_CLIENT_ID": DISCOVERY_CLIENT_ID,
    "DISCOVERY_CLIENT_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
}
ENV = {**AUTH_ENV, **DISCOVERY_ENV}


def access_claims(**overrides: Any) -> dict[str, Any]:
    """Cognito の人間用アクセストークンの claims（authorizer が検証済みの形）。"""
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "11111111-2222-3333-4444-555555555555",
        "client_id": HUMAN_CLIENT_ID,
        "token_use": "access",
        "scope": "openid",
        "username": "alice",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def http_event(
    method: str = "GET",
    path: str = "/api/me",
    claims: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """API Gateway HTTP API（payload format 2.0）のイベントを組み立てる。"""
    request_context: dict[str, Any] = {
        "requestId": "req-123",
        "http": {"method": method, "path": path},
    }
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims, "scopes": None}}
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "headers": headers or {},
        "requestContext": request_context,
        "isBase64Encoded": False,
    }


def call(event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    handler = create_management_handler(ENV)
    response = handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def test_human_access_token_returns_principal_from_iss_and_sub() -> None:
    status, body = call(http_event(claims=access_claims()))

    assert status == 200
    assert body["principal"] == {
        "type": "human",
        "issuer": ISSUER,
        "subject": "11111111-2222-3333-4444-555555555555",
    }
    assert body["roles"] == ["Viewer"]
    assert body["teams"] == []


@pytest.mark.parametrize(
    "groups",
    [
        # HTTP API の JWT authorizer は配列の claim を "[a b]" 形式の文字列に変換して渡す
        "[team-payments registry-curator registry-publisher registry-admin team-cards]",
        ["team-payments", "registry-curator", "registry-publisher", "registry-admin", "team-cards"],
    ],
)
def test_roles_and_teams_are_derived_from_cognito_groups(groups: Any) -> None:
    status, body = call(http_event(claims=access_claims(**{"cognito:groups": groups})))

    assert status == 200
    assert body["roles"] == ["Viewer", "Publisher", "Curator", "Admin"]
    assert body["teams"] == ["team-cards", "team-payments"]


def test_unknown_groups_are_ignored_and_caller_is_viewer() -> None:
    status, body = call(
        http_event(claims=access_claims(**{"cognito:groups": "[us-east-1_EXAMPLE_Google other]"}))
    )

    assert status == 200
    assert body["roles"] == ["Viewer"]
    assert body["teams"] == []


def test_request_without_token_is_rejected_with_common_error_dto() -> None:
    status, body = call(http_event(claims=None))

    assert status == 401
    assert body == {
        "error": {
            "code": "UNAUTHENTICATED",
            "message": "認証が必要です。ログインし直してください。",
            "requestId": "req-123",
        }
    }


@pytest.mark.parametrize(
    "claims",
    [
        # ID トークン（token_use が access でない）。ID トークンは client_id ではなく aud を持つ
        access_claims(token_use="id", client_id=None, aud=HUMAN_CLIENT_ID),
        # 別 issuer のトークン
        access_claims(iss="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER"),
        # sub がないトークン
        access_claims(sub=None),
    ],
    ids=["id-token", "other-issuer", "no-sub"],
)
def test_tokens_that_cannot_authenticate_a_human_are_rejected_with_401(
    claims: dict[str, Any],
) -> None:
    status, body = call(http_event(claims=claims))

    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert body["error"]["requestId"] == "req-123"
    assert "principal" not in body


@pytest.mark.parametrize(
    "claims",
    [
        # 別の client のアクセストークン
        access_claims(client_id="some-other-client"),
        # 発見用 M2M client のアクセストークン（client credentials。sub は client_id）
        access_claims(
            client_id=DISCOVERY_CLIENT_ID,
            sub=DISCOVERY_CLIENT_ID,
            username=None,
            scope="registry/discovery.read",
        ),
    ],
    ids=["other-client", "discovery-m2m"],
)
def test_valid_tokens_of_other_clients_are_forbidden_with_403(claims: dict[str, Any]) -> None:
    # 正規に発行されたトークンなので、ログインし直しても解決しない。401 ではなく 403 で拒否する
    status, body = call(http_event(claims=claims))

    assert status == 403
    assert body == {
        "error": {
            "code": "FORBIDDEN",
            "message": "このクライアントのトークンはポータル API では受け付けません。",
            "requestId": "req-123",
        }
    }


def test_unexpected_exception_returns_internal_error_dto_without_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_route(caller: Any, event: Any) -> Any:
        raise RuntimeError("内部の詳細: arn:aws:secret")

    monkeypatch.setitem(app_module.MANAGEMENT_ROUTES, "GET /api/me", broken_route)

    status, body = call(http_event(claims=access_claims()))

    assert status == 500
    assert body == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "内部エラーが発生しました。時間をおいて再度お試しください。",
            "requestId": "req-123",
        }
    }


@pytest.mark.parametrize(
    "authorizer",
    ["not-a-mapping", ["jwt"], {"jwt": "not-a-mapping"}, {"jwt": {"claims": "[a b]"}}],
    ids=["string", "list", "jwt-string", "claims-string"],
)
def test_malformed_authorizer_context_is_rejected_with_401(authorizer: Any) -> None:
    # 想定外の型でも 500 にせず、トークンなしと同じく 401 にする
    event = http_event(claims=None)
    event["requestContext"]["authorizer"] = authorizer

    status, body = call(event)

    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_x_user_email_header_does_not_change_principal() -> None:
    claims = access_claims(**{"cognito:groups": "[registry-publisher team-payments]"})
    _, without_header = call(http_event(claims=claims))
    status, with_header = call(
        http_event(
            claims=claims,
            headers={"x-user-email": "admin@example.com", "x-user-groups": "registry-admin"},
        )
    )

    assert status == 200
    assert with_header == without_header


def test_unknown_route_returns_not_found_with_common_error_dto() -> None:
    status, body = call(http_event(path="/api/unknown", claims=access_claims()))

    assert status == 404
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "指定された API は存在しません。"


@pytest.mark.parametrize(
    "environ",
    [
        DISCOVERY_ENV,
        {**DISCOVERY_ENV, "COGNITO_ISSUER": ISSUER},
        {**DISCOVERY_ENV, "HUMAN_CLIENT_ID": HUMAN_CLIENT_ID},
        {**DISCOVERY_ENV, "COGNITO_ISSUER": " ", "HUMAN_CLIENT_ID": HUMAN_CLIENT_ID},
    ],
    ids=["none", "no-client-id", "no-issuer", "blank-issuer"],
)
def test_missing_auth_settings_fail_at_startup(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError, match="認証設定"):
        create_management_handler(environ)


@pytest.mark.parametrize(
    "overrides",
    [
        {"REGISTRY_ARN": ""},
        {"DISCOVERY_TOKEN_URL": ""},
        {"DISCOVERY_CLIENT_ID": ""},
        {"DISCOVERY_CLIENT_SECRET_ARN": ""},
        # Registry ARN 以外（ID だけ、別サービスの ARN）は受け付けない
        {"REGISTRY_ARN": "abcdEFGH1234"},
        {"REGISTRY_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:registry/abcdEFGH1234"},
        # トークンエンドポイントは https に限る
        {"DISCOVERY_TOKEN_URL": "http://discovery.example.com/oauth2/token"},
        # secret は Secrets Manager の ARN で指定する（名前だけは受け付けない）
        {"DISCOVERY_CLIENT_SECRET_ARN": "discovery-m2m"},
    ],
    ids=[
        "no-registry",
        "no-token-url",
        "no-client-id",
        "no-secret",
        "registry-id-only",
        "old-namespace",
        "http-token-url",
        "secret-name-only",
    ],
)
def test_missing_or_invalid_discovery_settings_fail_at_startup(overrides: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError, match="発見設定"):
        create_management_handler({**ENV, **overrides})


def test_lambda_entrypoint_fails_to_load_without_auth_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 開発用に認証を通す経路は持たない。設定がなければ Lambda の初期化自体を失敗させる
    monkeypatch.delenv("COGNITO_ISSUER", raising=False)
    monkeypatch.delenv("HUMAN_CLIENT_ID", raising=False)
    sys.modules.pop("registry_portal.handlers.management", None)

    with pytest.raises(ConfigurationError):
        importlib.import_module("registry_portal.handlers.management")


def test_lambda_entrypoint_serves_requests_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("registry_portal.handlers.management", None)

    module = importlib.import_module("registry_portal.handlers.management")
    response = module.handler(http_event(claims=access_claims()), None)

    assert response["statusCode"] == 200
