"""Lambda handler の組み立て。

handler は API Gateway HTTP API（payload format 2.0）のイベントを受け取る。
検証済みの claims は `requestContext.authorizer.jwt.claims` から読む。
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from registry_portal.auth import Caller, authorize
from registry_portal.catalog import get_catalog_detail, list_catalog, search_catalog
from registry_portal.config import load_auth_settings, load_discovery_settings
from registry_portal.errors import ApiError, RequestValidationError, RouteNotFoundError
from registry_portal.services.catalog_supplement import CatalogSupplement, SupplementClient
from registry_portal.services.discovery import DiscoveryDependencies, create_discovery_client

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


def _json_body(event: LambdaEvent) -> Mapping[str, Any]:
    error = RequestValidationError("リクエスト本文には JSON オブジェクトを指定してください。")
    raw = event.get("body")
    if not isinstance(raw, str) or len(raw) > 8192:
        raise error
    try:
        body = json.loads(
            base64.b64decode(raw, validate=True) if event.get("isBase64Encoded") else raw
        )
    except (ValueError, RecursionError):
        raise error from None
    if not isinstance(body, Mapping):
        raise error
    return body


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
    discovery: DiscoveryDependencies | None = None,
    supplement_client: SupplementClient | None = None,
) -> Handler:
    """管理 handler を作る。認証・発見の設定が不足していれば、ここで起動エラーにする。

    discovery（発見アダプターの外部部品）はテストでフェイクに差し替える。
    通信は最初のリクエストまで行わない。
    """
    settings = load_auth_settings(environ)
    discovery_settings = load_discovery_settings(environ)
    discovery_client = create_discovery_client(
        discovery_settings, discovery or DiscoveryDependencies()
    )
    table_name = environ.get("CATALOG_REFERENCES_TABLE_NAME", "").strip()
    supplement = (
        CatalogSupplement(table_name, discovery_settings, supplement_client) if table_name else None
    )

    def get_catalog(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        # Viewer（ログインした全員）が閲覧できる。発見用 M2M トークンでポータルが代理で問い合わせる
        return list_catalog(discovery_client, _query_of(event))

    def post_search(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("検索条件はリクエスト本文に指定してください。")
        return search_catalog(discovery_client, _json_body(event))

    routes: Mapping[str, Route] = {
        **MANAGEMENT_ROUTES,
        "GET /api/catalog": get_catalog,
        "POST /api/catalog/search": post_search,
    }

    def handler(event: LambdaEvent, context: Any) -> LambdaResponse:
        request_id = _request_context(event).get("requestId")
        try:
            caller = authorize(_claims_of(event), settings)
            key = _route_key(event)
            route = routes.get(key)
            detail_prefix = "GET /api/catalog/"
            record_id = key.removeprefix(detail_prefix)
            if route is not None:
                status, body = route(caller, event)
            elif key.startswith(detail_prefix) and record_id and "/" not in record_id:
                if _query_of(event):
                    raise RequestValidationError(
                        "カタログ詳細にクエリパラメーターは指定できません。"
                    )
                status, body = get_catalog_detail(discovery_client, record_id, supplement)
            else:
                raise RouteNotFoundError()
            return _json_response(status, body)
        except ApiError as error:
            # ログにはトークンや claims の全文を出さない
            logger.info("API エラー: code=%s request_id=%s", error.code, request_id)
            return _json_response(error.status, error.to_dto(request_id))
        except Exception:
            logger.exception("想定外のエラー: request_id=%s", request_id)
            return _json_response(500, ApiError().to_dto(request_id))

    return handler
