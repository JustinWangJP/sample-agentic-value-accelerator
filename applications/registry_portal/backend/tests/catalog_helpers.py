"""カタログ API の Seam 1 テストで共有するフェイクと fixture。

発見 API とトークンエンドポイントは HTTP トランスポートのフェイク、Secrets Manager と
DynamoDB は Botocore の Stubber（実サービスモデル）または moto に差し替える。
"""

from __future__ import annotations

import base64
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from registry_portal.app import create_management_handler
from registry_portal.services.discovery import DiscoveryDependencies
from registry_portal.services.http import TransportError, TransportResponse

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE"
HUMAN_CLIENT_ID = "human-spa-client"
REGISTRY_ARN = "arn:aws:agent-registry:us-east-1:123456789012:registry/abcdEFGH1234"
TOKEN_URL = "https://discovery.auth.us-east-1.amazoncognito.com/oauth2/token"
SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-m2m-AbCdEf"
CLIENT_SECRET = "s3cr3t-client-value"

ENV = {
    "COGNITO_ISSUER": ISSUER,
    "HUMAN_CLIENT_ID": HUMAN_CLIENT_ID,
    "REGISTRY_ARN": REGISTRY_ARN,
    "DISCOVERY_TOKEN_URL": TOKEN_URL,
    "DISCOVERY_CLIENT_ID": "discovery-m2m-client",
    "DISCOVERY_CLIENT_SECRET_ARN": SECRET_ARN,
    "DISCOVERY_SCOPE": "registry/discovery.read",
}

LIST_URL = (
    "https://agent-registry.us-east-1.api.aws/registries/"
    "arn%3Aaws%3Aagent-registry%3Aus-east-1%3A123456789012%3Aregistry%2FabcdEFGH1234"
    "/discoverable-records-list"
)
SEARCH_URL = "https://agent-registry.us-east-1.api.aws/discoverable-records-search"
BATCH_URL = "https://agent-registry.us-east-1.api.aws/discoverable-records-batch"
SUPPLEMENT_TABLE = "registry-auxiliary"


@dataclass
class SentRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None

    def json(self) -> Any:
        assert self.body is not None
        return json.loads(self.body)


def json_response(status: int, body: Any) -> TransportResponse:
    return TransportResponse(status=status, body=json.dumps(body).encode())


def summary(record_id: str, record_type: str = "AGENT", **overrides: Any) -> dict[str, Any]:
    """ListDiscoverableRegistryRecords が返す DiscoverableRegistryRecordSummary。"""
    record: dict[str, Any] = {
        "registryArn": REGISTRY_ARN,
        "recordArn": f"{REGISTRY_ARN}/record/{record_id}",
        "recordId": record_id,
        "name": f"name-{record_id}",
        "displayName": f"表示名 {record_id}",
        "description": f"説明 {record_id}",
        "recordType": record_type,
        "descriptorTypes": ["custom"],
        "recordVersion": "1.0.0",
        "status": "APPROVED",
        "createdAt": "2026-11-01T00:00:00Z",
        "updatedAt": "2026-11-02T00:00:00Z",
    }
    record.update(overrides)
    return record


@dataclass
class FakeTransport:
    """Cognito のトークンエンドポイントと、Registry の発見 API を模す。"""

    registry: deque[TransportResponse | TransportError] = field(default_factory=deque)
    tokens: deque[TransportResponse | TransportError] = field(default_factory=deque)
    requests: list[SentRequest] = field(default_factory=list)
    issued: int = 0

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse:
        self.requests.append(SentRequest(method, url, dict(headers), body))
        if url == TOKEN_URL:
            return self._next(self.tokens) if self.tokens else self._issue()
        if url in (LIST_URL, SEARCH_URL, BATCH_URL):
            return self._next(self.registry)
        raise AssertionError(f"想定外の宛先: {url}")

    def _issue(self) -> TransportResponse:
        self.issued += 1
        return json_response(
            200,
            {
                "access_token": f"m2m-token-{self.issued}",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    @staticmethod
    def _next(queue: deque[TransportResponse | TransportError]) -> TransportResponse:
        item = queue.popleft()
        if isinstance(item, TransportError):
            raise item
        return item

    def to(self, url: str) -> list[SentRequest]:
        return [r for r in self.requests if r.url.split("?")[0] == url.split("?")[0]]


class FakeSecrets:
    """Secrets Manager の GetSecretValue を Botocore Stubber で模す。

    応答は呼ばれる回数だけ積む。積んだ数より多く呼ばれると Stubber が失敗させる。
    """

    def __init__(self, *secret_strings: str, error_code: str | None = None) -> None:
        self.client = boto3.client(
            "secretsmanager",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        self.stubber = Stubber(self.client)
        if error_code is not None:
            self._fail(error_code)
        for secret_string in secret_strings or (() if error_code else (CLIENT_SECRET,)):
            self._respond(secret_string)
        self.stubber.activate()

    def _respond(self, secret_string: str) -> None:
        self.stubber.add_response(
            "get_secret_value",
            {"ARN": SECRET_ARN, "Name": "discovery-m2m", "SecretString": secret_string},
            expected_params={"SecretId": SECRET_ARN},
        )

    def _fail(self, code: str) -> None:
        self.stubber.add_client_error(
            "get_secret_value",
            service_error_code=code,
            service_message=f"詳細 {SECRET_ARN}",
            http_status_code=400,
            expected_params={"SecretId": SECRET_ARN},
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def access_claims() -> dict[str, Any]:
    return {
        "iss": ISSUER,
        "sub": "11111111-2222-3333-4444-555555555555",
        "client_id": HUMAN_CLIENT_ID,
        "token_use": "access",
    }


def catalog_event(
    query: dict[str, str] | None = None, claims: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": "GET /api/catalog",
        "rawPath": "/api/catalog",
        "headers": {},
        "queryStringParameters": query,
        "requestContext": {
            "requestId": "req-cat",
            "http": {"method": "GET", "path": "/api/catalog"},
            "authorizer": {"jwt": {"claims": claims or access_claims(), "scopes": None}},
        },
        "isBase64Encoded": False,
    }


def basic_credentials(request: SentRequest) -> str:
    return base64.b64decode(request.headers["authorization"].removeprefix("Basic ")).decode()


def make_handler(
    transport: FakeTransport,
    clock: Clock | None = None,
    secrets: FakeSecrets | None = None,
    *,
    env: Mapping[str, str] | None = None,
    supplement_client: Any = None,
) -> Any:
    """管理 handler を作る。env は ENV に上書きする値。"""
    return create_management_handler(
        {**ENV, **(env or {})},
        discovery=DiscoveryDependencies(
            transport=transport,
            clock=clock or Clock(),
            secrets_client=(secrets or FakeSecrets()).client,
        ),
        supplement_client=supplement_client,
    )


def call(handler: Any, event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def page(*records: dict[str, Any], next_token: str | None = None) -> TransportResponse:
    body: dict[str, Any] = {"registryRecords": list(records)}
    if next_token is not None:
        body["nextToken"] = next_token
    return json_response(200, body)


def botocore_discovery_request(operation: str, **params: Any) -> dict[str, Any]:
    """Botocore が送る直前のリクエストを取り出す（送信はしない）。"""
    client = boto3.client(
        "agent-registry",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    captured: dict[str, Any] = {}

    class _Captured(Exception):
        pass

    def capture(request: Any, **_: Any) -> None:
        captured.update(method=request.method, url=request.url, body=request.body)
        raise _Captured()

    api_name = client.meta.method_to_api_mapping[operation]
    client.meta.events.register(f"before-send.agent-registry.{api_name}", capture)
    with pytest.raises(_Captured):
        getattr(client, operation)(**params)
    return captured


def search_event(payload: Any, *, encode: bool = False) -> dict[str, Any]:
    event = catalog_event()
    event["routeKey"] = "POST /api/catalog/search"
    event["requestContext"]["http"] = {"method": "POST", "path": "/api/catalog/search"}
    body = json.dumps(payload).encode()
    event["body"] = base64.b64encode(body).decode() if encode else body.decode()
    event["isBase64Encoded"] = encode
    return event


def detail_event(record_id: str) -> dict[str, Any]:
    event = catalog_event()
    event["routeKey"] = "GET /api/catalog/{recordId}"
    event["requestContext"]["http"]["path"] = f"/api/catalog/{record_id}"
    event["pathParameters"] = {"recordId": record_id}
    return event


def hit(record_id: str, descriptor: str = "a2aAgentCard", **overrides: Any) -> dict[str, Any]:
    """検索・詳細 API が返す、descriptor の本体を持つ公開承認済みのレコード。"""
    record = summary(
        record_id,
        descriptors={descriptor: {"data": "{}"}},
        customMetadata={"isA2A": descriptor == "a2aAgentCard"},
    )
    del record["descriptorTypes"]
    record.update(overrides)
    return record


def found(*records: dict[str, Any]) -> TransportResponse:
    """BatchGetDiscoverableRegistryRecord の成功応答。"""
    return json_response(200, {"registryRecords": list(records), "errors": []})


def dynamodb_client() -> Any:
    return boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def supplement_key(record_id: str, sort_key: str) -> dict[str, Any]:
    """補助テーブルの GetItem に期待する引数。"""
    return {
        "TableName": SUPPLEMENT_TABLE,
        "ConsistentRead": True,
        "Key": {
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{record_id}"},
            "sk": {"S": sort_key},
        },
    }
