"""Lambda handler の組み立て。

handler は API Gateway HTTP API（payload format 2.0）のイベントを受け取る。
検証済みの claims は `requestContext.authorizer.jwt.claims` から読む。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from registry_portal.auth import Caller, authorize
from registry_portal.config import load_auth_settings
from registry_portal.errors import ApiError, RouteNotFoundError

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


def _request_context(event: LambdaEvent) -> Mapping[str, Any]:
    context = event.get("requestContext")
    return context if isinstance(context, Mapping) else {}


def _claims_of(event: LambdaEvent) -> Mapping[str, Any] | None:
    authorizer = _request_context(event).get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims")
    return claims if isinstance(claims, Mapping) else None


def _route_key(event: LambdaEvent) -> str:
    http = _request_context(event).get("http") or {}
    return f"{http.get('method', '')} {http.get('path', '')}"


def get_me(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
    """自分の主体・業務ロール・所属チームを返す。"""
    return 200, {
        "principal": {
            "type": "human",
            "issuer": caller.principal.issuer,
            "subject": caller.principal.subject,
        },
        "roles": list(caller.roles),
        "teams": list(caller.teams),
    }


MANAGEMENT_ROUTES: Mapping[str, Route] = {
    "GET /api/me": get_me,
}


def create_management_handler(environ: Mapping[str, str]) -> Handler:
    """管理 handler を作る。認証設定が不足していれば、ここで起動エラーにする。"""
    settings = load_auth_settings(environ)

    def handler(event: LambdaEvent, context: Any) -> LambdaResponse:
        request_id = _request_context(event).get("requestId")
        try:
            caller = authorize(_claims_of(event), settings)
            route = MANAGEMENT_ROUTES.get(_route_key(event))
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
