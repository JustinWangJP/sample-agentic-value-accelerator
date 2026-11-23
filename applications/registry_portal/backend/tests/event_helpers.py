"""API Gateway REST API（Lambda プロキシ統合、Cognito User Pools authorizer）のイベント。

形は公式文書の Lambda プロキシ統合の入力形式に合わせる
（https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html）。

- メソッドとパスは最上位の `httpMethod` と `path`。`path` にステージ名は入らず、
  `requestContext.path` には入る（authorizer の入力例: api-gateway-lambda-authorizer-input.html）
- 検証済みの claims は `requestContext.authorizer.claims`（`$context.authorizer.claims.*`）。
  トークンがなければ `claims` は null
"""

from __future__ import annotations

from typing import Any

STAGE = "v1"


def rest_api_event(
    method: str = "GET",
    path: str = "/api/me",
    claims: dict[str, Any] | None = None,
    *,
    resource: str | None = None,
    path_parameters: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """REST API の Lambda プロキシ統合のイベントを組み立てる。"""
    return {
        "resource": resource or path,
        "path": path,
        "httpMethod": method,
        "headers": headers or {},
        "multiValueHeaders": {name: [value] for name, value in (headers or {}).items()},
        "queryStringParameters": None,
        "multiValueQueryStringParameters": None,
        "pathParameters": path_parameters,
        "stageVariables": None,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "a1b2c3",
            "authorizer": {"claims": claims, "scopes": None},
            "httpMethod": method,
            "path": f"/{STAGE}{path}",
            "requestId": "req-rest",
            "resourcePath": resource or path,
            "stage": STAGE,
        },
        "body": None,
        "isBase64Encoded": False,
    }
