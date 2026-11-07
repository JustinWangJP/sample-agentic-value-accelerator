"""Lambda handler の組み立て。

handler は API Gateway HTTP API（payload format 2.0）のイベントを受け取る。
検証済みの claims は `requestContext.authorizer.jwt.claims` から読む。
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any, cast

import boto3

from registry_portal.auth import Caller, authorize
from registry_portal.catalog import get_catalog_detail, list_catalog, search_catalog
from registry_portal.config import (
    load_auth_settings,
    load_catalog_settings,
    load_discovery_settings,
)
from registry_portal.errors import ApiError, RequestValidationError, RouteNotFoundError
from registry_portal.drafts import (
    AgentDrafts,
    DraftsNotConfigured,
    RegistryControlClient,
    require_publisher,
)
from registry_portal.services.catalog_supplement import CatalogSupplement, SupplementClient
from registry_portal.services.discovery import DiscoveryDependencies, create_discovery_client
from registry_portal.services.draft_store import DraftStore, DraftStoreClient

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


# path に1つの要素を含むルート。(実際の path の接頭辞, ルート表のキー)
_PARAMETERIZED_ROUTES = (
    ("GET /api/catalog/", "GET /api/catalog/{recordId}"),
    ("GET /api/records/", "GET /api/records/{recordId}"),
    ("PATCH /api/records/", "PATCH /api/records/{recordId}"),
    ("GET /api/operations/", "GET /api/operations/{operationId}"),
)


def _route_template(key: str) -> tuple[str, str]:
    """ルート表のキーと、path パラメーター（なければ空文字）を返す。"""
    for prefix, template in _PARAMETERIZED_ROUTES:
        parameter = key.removeprefix(prefix)
        if key.startswith(prefix) and parameter and "/" not in parameter:
            return template, parameter
    return key, ""


def _path_parameter(event: LambdaEvent) -> str:
    return _route_template(_route_key(event))[1]


def _query_of(event: LambdaEvent) -> Mapping[str, str]:
    """クエリ文字列。HTTP API は同じ名前の値をカンマで連結した1つの文字列にして渡す。"""
    query = _mapping_or_empty(event.get("queryStringParameters"))
    return {str(key): str(value) for key, value in query.items()}


# 下書きの本文の上限。項目ごとの文字数上限（domain/drafts.py の TEXT_LIMITS）の合計に余裕を持たせる
DRAFT_BODY_MAX_LENGTH = 262144


def _json_body(event: LambdaEvent, max_length: int = 8192) -> Mapping[str, Any]:
    error = RequestValidationError("リクエスト本文には JSON オブジェクトを指定してください。")
    raw = event.get("body")
    if not isinstance(raw, str) or len(raw) > max_length:
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
    registry_client: RegistryControlClient | None = None,
    draft_store_client: DraftStoreClient | None = None,
) -> Handler:
    """管理 handler を作る。認証・発見の設定が不足していれば、ここで起動エラーにする。

    discovery（発見アダプターの外部部品）と AWS クライアントはテストで差し替える。
    通信は最初のリクエストまで行わない。
    """
    settings = load_auth_settings(environ)
    discovery_settings = load_discovery_settings(environ)
    catalog_settings = load_catalog_settings(environ)
    discovery_client = create_discovery_client(
        discovery_settings, discovery or DiscoveryDependencies()
    )
    supplement = (
        CatalogSupplement(
            catalog_settings.supplement_table_name,
            discovery_settings.registry_arn,
            discovery_settings.registry_region,
            supplement_client,
        )
        if catalog_settings.supplement_table_name
        else None
    )
    region = discovery_settings.registry_region
    # 下書きは補助テーブルが必須。未設定でもカタログは提供し、下書きの操作だけを 503 にする
    drafts = (
        AgentDrafts(
            discovery_settings.registry_arn,
            DraftStore(
                catalog_settings.supplement_table_name,
                discovery_settings.registry_arn,
                draft_store_client
                or cast(DraftStoreClient, boto3.client("dynamodb", region_name=region)),
            ),
            registry_client
            or cast(
                RegistryControlClient,
                boto3.client("agent-registry-control", region_name=region),
            ),
        )
        if catalog_settings.supplement_table_name
        else None
    )

    def drafts_for(caller: Caller) -> AgentDrafts:
        # 権限のない呼び出し元には、設定の状態より先に 403 を返す
        require_publisher(caller)
        if drafts is None:
            raise DraftsNotConfigured()
        return drafts

    # カタログは Viewer（ログインした全員）が閲覧できる。発見用 M2M トークンでポータルが代理で問い合わせる
    def get_catalog(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return list_catalog(discovery_client, _query_of(event))

    def post_search(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("検索条件はリクエスト本文に指定してください。")
        return search_catalog(
            discovery_client,
            _json_body(event),
            a2a_search_enabled=catalog_settings.a2a_search_enabled,
        )

    def get_detail(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("カタログ詳細にクエリパラメーターは指定できません。")
        return get_catalog_detail(discovery_client, _path_parameter(event), supplement)

    def post_record(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        headers = _mapping_or_empty(event.get("headers"))
        key = next(
            (str(value) for name, value in headers.items() if name.lower() == "idempotency-key"),
            "",
        )
        return drafts_for(caller).create(caller, _json_body(event, DRAFT_BODY_MAX_LENGTH), key)

    def get_record(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).get(caller, _path_parameter(event))

    def list_records(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).list_drafts(caller, _query_of(event))

    def patch_record(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).update(
            caller, _path_parameter(event), _json_body(event, DRAFT_BODY_MAX_LENGTH)
        )

    def get_operation(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).operation(caller, _path_parameter(event))

    routes: Mapping[str, Route] = {
        **MANAGEMENT_ROUTES,
        "GET /api/catalog": get_catalog,
        "POST /api/catalog/search": post_search,
        "GET /api/catalog/{recordId}": get_detail,
        "POST /api/records": post_record,
        "GET /api/records": list_records,
        "GET /api/records/{recordId}": get_record,
        "PATCH /api/records/{recordId}": patch_record,
        "GET /api/operations/{operationId}": get_operation,
    }

    def handler(event: LambdaEvent, context: Any) -> LambdaResponse:
        request_id = _request_context(event).get("requestId")
        try:
            caller = authorize(_claims_of(event), settings)
            route = routes.get(_route_template(_route_key(event))[0])
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
