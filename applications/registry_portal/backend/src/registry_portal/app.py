"""Lambda handler の組み立て。

handler は API Gateway HTTP API（payload format 2.0）のイベントを受け取る。
検証済みの claims は `requestContext.authorizer.jwt.claims` から読む。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from registry_portal.auth import Caller, authorize
from registry_portal.catalog import CatalogDiscovery, list_catalog
from registry_portal.config import load_auth_settings, load_discovery_settings
from registry_portal.errors import ApiError, RouteNotFoundError
from registry_portal.services.discovery import Clock, create_discovery_client
from registry_portal.services.http import Transport, urllib_transport

logger = logging.getLogger(__name__)

LambdaEvent = Mapping[str, Any]
LambdaResponse = dict[str, Any]
Handler = Callable[[LambdaEvent, Any], LambdaResponse]
Route = Callable[[Caller, LambdaEvent], tuple[int, Mapping[str, Any]]]


def _json_response(status: int, body: Mapping[str, Any]) -> LambdaResponse:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    """想定外の型（文字列・配列など）は空として扱い、500 ではなく認証エラーにする。"""
    return value if isinstance(value, Mapping) else {}


def _request_context(event: LambdaEvent) -> Mapping[str, Any]:
    return _mapping_or_empty(event.get("requestContext"))


def _claims_of(event: LambdaEvent) -> Mapping[str, Any] | None:
    authorizer = _mapping_or_empty(_request_context(event).get("authorizer"))
    claims = _mapping_or_empty(authorizer.get("jwt")).get("claims")
    return claims if isinstance(claims, Mapping) else None


def _route_key(event: LambdaEvent) -> str:
    http = _mapping_or_empty(_request_context(event).get("http"))
    return f"{http.get('method', '')} {http.get('path', '')}"


def _query_of(event: LambdaEvent) -> Mapping[str, str]:
    """クエリ文字列。HTTP API は同じ名前の値をカンマで連結した1つの文字列にして渡す。"""
    query = _mapping_or_empty(event.get("queryStringParameters"))
    return {str(key): str(value) for key, value in query.items()}


def get_me(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
    """自分の主体・業務ロール・所属チームを返す。"""
    return 200, {
        "principal": {
            "type": "human",
            "issuer": caller.principal.issuer,
            "subject": caller.principal.subject,
        },
        "roles": [role.value for role in caller.roles],
        "teams": list(caller.teams),
    }


MANAGEMENT_ROUTES: Mapping[str, Route] = {
    "GET /api/me": get_me,
}


def create_management_handler(
    environ: Mapping[str, str],
    *,
    transport: Transport = urllib_transport,
    clock: Clock = time.monotonic,
) -> Handler:
    """管理 handler を作る。認証・発見の設定が不足していれば、ここで起動エラーにする。

    transport と clock はテストでフェイクに差し替える。通信は最初のリクエストまで行わない。
    """
    settings = load_auth_settings(environ)
    discovery: CatalogDiscovery = create_discovery_client(
        load_discovery_settings(environ), transport, clock, environ
    )

    def get_catalog(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        # Viewer（ログインした全員）が閲覧できる。発見用 M2M トークンでポータルが代理で問い合わせる
        return list_catalog(discovery, _query_of(event))

    routes: Mapping[str, Route] = {**MANAGEMENT_ROUTES, "GET /api/catalog": get_catalog}

    def handler(event: LambdaEvent, context: Any) -> LambdaResponse:
        request_id = _request_context(event).get("requestId")
        try:
            caller = authorize(_claims_of(event), settings)
            route = routes.get(_route_key(event))
            if route is None:
                raise RouteNotFoundError()
            status, body = route(caller, event)
            return _json_response(status, body)
        except ApiError as error:
            # ログにはトークンや claims の全文を出さない
            logger.info("API エラー: code=%s request_id=%s", error.code, request_id)
            return _json_response(error.status, error.to_dto(request_id))
        except Exception:
            logger.exception("想定外のエラー: request_id=%s", request_id)
            return _json_response(500, ApiError().to_dto(request_id))

    return handler
