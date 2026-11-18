"""下書き API の Seam 1 テストで共有するイベント・クライアント・Registry レコードの fixture。

Registry 管理 API は `agent-registry-control` の実サービスモデルに対する botocore の Stubber、
補助テーブルは moto に差し替える。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import boto3

from catalog_helpers import ENV, HUMAN_CLIENT_ID, ISSUER, REGISTRY_ARN, SUPPLEMENT_TABLE
from registry_portal.app import create_management_handler

RECORD_ID = "agentABC1234"
RECORD_ARN = f"{REGISTRY_ARN}/record/{RECORD_ID}"


def event(
    method: str = "POST",
    path: str = "/api/records",
    body: Any = None,
    groups: str = "registry-publisher team-payments",
    key: str = "draft-1",
    sub: str = "alice",
) -> dict[str, Any]:
    return {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {
                "jwt": {
                    "claims": {
                        "iss": ISSUER,
                        "sub": sub,
                        "client_id": HUMAN_CLIENT_ID,
                        "token_use": "access",
                        "cognito:groups": groups,
                    }
                }
            },
        },
        "headers": {"idempotency-key": key} if key else {},
        "body": json.dumps(body or {}),
    }


def call(handler: Any, request: dict[str, Any]) -> tuple[int, Any]:
    response = handler(request, None)
    return response["statusCode"], json.loads(response["body"])


def clients() -> tuple[Any, Any]:
    opts = {
        "region_name": "us-east-1",
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    registry = boto3.client("agent-registry-control", **opts)
    db = boto3.client("dynamodb", **opts)
    db.create_table(
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
    return registry, db


# GetRegistry の approvalConfiguration と、接続情報・申請が共有する自動承認の判定（AWS の定義では
# 省略・空は手動審査）。型違いの応答は Stubber のモデル検査を通らないため ConfiguredRegistry で返す
AUTO_APPROVAL_CASES: list[tuple[dict[str, Any], str]] = [
    ({}, "DISABLED"),
    ({"approvalConfiguration": {}}, "DISABLED"),
    ({"approvalConfiguration": {"autoApprovalRules": []}}, "DISABLED"),
    ({"approvalConfiguration": {"autoApprovalRules": ["APPROVE_ALL"]}}, "ENABLED"),
    ({"approvalConfiguration": {"autoApprovalRules": "APPROVE_ALL"}}, "UNKNOWN"),
    ({"approvalConfiguration": []}, "UNKNOWN"),
]


class ConfiguredRegistry:
    """GetRegistry だけを任意の応答（または例外）に差し替え、ほかの API は元のクライアントに委ねる。

    Stubber はサービスモデルに合わない応答を拒否するため、型違いの設定や通信例外の再現に使う。
    """

    def __init__(self, client: Any, response: Mapping[str, Any] | Exception) -> None:
        self._client = client
        self._response = response

    def get_registry(self, **_: Any) -> Mapping[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def handler_for(registry: Any, db: Any) -> Any:
    return create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        supplement_store_client=db,
    )


def management_item(db: Any, record_id: str = RECORD_ID) -> dict[str, Any]:
    return db.get_item(
        TableName=SUPPLEMENT_TABLE,
        Key={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#RECORD#{record_id}"},
            "sk": {"S": "MANAGEMENT"},
        },
    )["Item"]


def aws_record(draft: dict[str, str], **changes: Any) -> dict[str, Any]:
    """GetRegistryRecord の応答。`changes` で AWS が返す項目を上書きする。"""
    return {
        "registryArn": REGISTRY_ARN,
        "recordArn": RECORD_ARN,
        "recordId": RECORD_ID,
        "name": draft["name"],
        "displayName": draft["displayName"],
        "description": draft["description"],
        "recordType": draft["recordType"],
        "recordVersion": draft["recordVersion"],
        "status": "DRAFT",
        "createdAt": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "updatedAt": datetime(2026, 11, 1, tzinfo=timezone.utc),
        **changes,
    }
