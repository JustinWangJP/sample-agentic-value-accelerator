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
    AuthSettings,
    ConfigurationError,
    load_auth_settings,
    load_catalog_settings,
    load_discovery_settings,
    load_registry_settings,
)
from registry_portal.curation import DECISION_BODY_MAX_LENGTH, RecordCuration
from registry_portal.curation_reads import CurationReads, RegistryStatusClient
from registry_portal.deprecation import (
    DEPRECATION_BODY_MAX_LENGTH,
    DeprecationRequests,
)
from registry_portal.domain.curation import LIFECYCLE_ACTIONS
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    REJECT_KIND,
    OperationKind,
    is_decision_operation,
)
from registry_portal.errors import ApiError, RequestValidationError, RouteNotFoundError
from registry_portal.drafts import (
    DraftSettlement,
    RecordDrafts,
    DraftsNotConfigured,
    RegistryControlClient,
    require_publisher,
)
from registry_portal.history import RecordHistory
from registry_portal.lifecycle import RecordLifecycle
from registry_portal.recovery import RECOVERY_BODY_MAX_LENGTH, OperationRecovery
from registry_portal.review_queue import ReviewQueue
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


# path に1つの要素を含むルート。(実際の path の接頭辞, 接尾辞, ルート表のキー)
_PARAMETERIZED_ROUTES = (
    ("GET /api/catalog/", "", "GET /api/catalog/{recordId}"),
    ("GET /api/records/", "", "GET /api/records/{recordId}"),
    ("GET /api/records/", "/history", "GET /api/records/{recordId}/history"),
    ("PATCH /api/records/", "", "PATCH /api/records/{recordId}"),
    ("POST /api/records/", "/submit", "POST /api/records/{recordId}/submit"),
    (
        "POST /api/records/",
        "/deprecation-requests",
        "POST /api/records/{recordId}/deprecation-requests",
    ),
    (
        "POST /api/records/",
        "/deprecation-requests/decline",
        "POST /api/records/{recordId}/deprecation-requests/decline",
    ),
    *(
        ("POST /api/records/", f"/{action}", f"POST /api/records/{{recordId}}/{action}")
        for action in LIFECYCLE_ACTIONS
    ),
    ("GET /api/records/", "/lifecycle", "GET /api/records/{recordId}/lifecycle"),
    ("GET /api/records/", "/recovery", "GET /api/records/{recordId}/recovery"),
    ("POST /api/records/", "/recovery", "POST /api/records/{recordId}/recovery"),
    ("GET /api/operations/", "", "GET /api/operations/{operationId}"),
    ("GET /api/reviews/", "", "GET /api/reviews/{recordId}"),
    ("POST /api/reviews/", "/approve", "POST /api/reviews/{recordId}/approve"),
    ("POST /api/reviews/", "/reject", "POST /api/reviews/{recordId}/reject"),
)


def _route_template(key: str) -> tuple[str, str]:
    """ルート表のキーと、path パラメーター（なければ空文字）を返す。"""
    for prefix, suffix, template in _PARAMETERIZED_ROUTES:
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        parameter = key[len(prefix) : len(key) - len(suffix)]
        if parameter and "/" not in parameter:
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
# 申請の本文の上限。申請理由（4096文字）と関連参照（20件）の上限に余裕を持たせる
SUBMISSION_BODY_MAX_LENGTH = 16384


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


def _idempotency_key(event: LambdaEvent) -> str:
    """Idempotency-Key ヘッダーの値。ヘッダー名の大文字・小文字は区別しない。なければ空文字。"""
    headers = _mapping_or_empty(event.get("headers"))
    return next(
        (str(value) for name, value in headers.items() if name.lower() == "idempotency-key"), ""
    )


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


def _serve(routes: Mapping[str, Route], settings: AuthSettings) -> Handler:
    """ルート表を Lambda handler にする。認可・エラー応答はすべての handler で共通。"""

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
    drafts = None
    # 決裁操作の照会（GET /api/operations）は管理 handler が担う。決裁自体は決裁 handler が行うため、
    # このインスタンスは AWS の状態更新 API を呼び出さない（IAM でも拒否される）
    curation = None
    history_service = None
    deprecation_requests = None
    if catalog_settings.supplement_table_name:
        control_client = registry_client or cast(
            RegistryControlClient,
            boto3.client("agent-registry-control", region_name=region),
        )
        store = DraftStore(
            catalog_settings.supplement_table_name,
            discovery_settings.registry_arn,
            draft_store_client
            or cast(DraftStoreClient, boto3.client("dynamodb", region_name=region)),
        )
        drafts = RecordDrafts(
            discovery_settings.registry_arn, store, control_client, discovery_client
        )
        # 管理 handler の Registry クライアントは状態更新 API を持たない（IAM でも拒否される）。
        # 操作照会は AWS を呼び直さないため、型の上だけ決裁 handler と揃える
        status_client = cast(RegistryStatusClient, control_client)
        reads = CurationReads(discovery_settings.registry_arn, store, status_client)
        curation = RecordCuration(reads, store, status_client)
        history_service = RecordHistory(store)
        deprecation_requests = DeprecationRequests(reads, store)

    def drafts_for(caller: Caller) -> RecordDrafts:
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
        return drafts_for(caller).create(
            caller, _json_body(event, DRAFT_BODY_MAX_LENGTH), _idempotency_key(event)
        )

    def get_record(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).get(caller, _path_parameter(event))

    def get_history(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        # 履歴の閲覧は Publisher（所有チーム）・Curator・Admin に開くため、閲覧の認可は履歴 API の内側で行う
        if history_service is None:
            raise DraftsNotConfigured()
        return history_service.history(caller, _path_parameter(event))

    def list_records(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).list_drafts(caller, _query_of(event))

    def patch_record(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).update(
            caller, _path_parameter(event), _json_body(event, DRAFT_BODY_MAX_LENGTH)
        )

    def submit_record(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return drafts_for(caller).submit(
            caller,
            _path_parameter(event),
            _json_body(event, SUBMISSION_BODY_MAX_LENGTH),
            _idempotency_key(event),
        )

    def request_deprecation(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        # 廃止依頼も Publisher の書き込み操作。権限のない呼び出し元には設定の状態より先に 403 を返す
        require_publisher(caller)
        if deprecation_requests is None:
            raise DraftsNotConfigured()
        return deprecation_requests.create(
            caller, _path_parameter(event), _json_body(event, DEPRECATION_BODY_MAX_LENGTH)
        )

    def get_operation(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        # 操作の種別で参照できる主体が変わる（決裁は決裁した Curator、下書きは所有チームの Publisher）。
        # 認可は種別ごとの照会の内側で行う
        if drafts is None or curation is None:
            raise DraftsNotConfigured()
        operation = drafts.find_operation(_path_parameter(event))
        if is_decision_operation(operation):
            return curation.operation_status(caller, operation)
        return drafts.operation_status(caller, operation)

    routes: Mapping[str, Route] = {
        **MANAGEMENT_ROUTES,
        "GET /api/catalog": get_catalog,
        "POST /api/catalog/search": post_search,
        "GET /api/catalog/{recordId}": get_detail,
        "POST /api/records": post_record,
        "GET /api/records": list_records,
        "GET /api/records/{recordId}": get_record,
        "GET /api/records/{recordId}/history": get_history,
        "PATCH /api/records/{recordId}": patch_record,
        "GET /api/operations/{operationId}": get_operation,
        "POST /api/records/{recordId}/submit": submit_record,
        "POST /api/records/{recordId}/deprecation-requests": request_deprecation,
    }

    return _serve(routes, settings)


def create_curation_handler(
    environ: Mapping[str, str],
    *,
    registry_client: RegistryStatusClient | None = None,
    store_client: DraftStoreClient | None = None,
) -> Handler:
    """決裁 handler を作る。認証設定と補助テーブルが不足していれば、ここで起動エラーにする。

    決裁 handler は審査待ちの一覧・詳細、公開承認・却下・公開停止・廃止、公開管理の照会、
    廃止依頼の見送り、Admin の回復操作を受け持つ。
    発見（JWT）の設定は使わないため、Registry の ARN とリージョンだけを読む。
    AWS クライアントはテストで差し替える。通信は最初のリクエストまで行わない。
    """
    settings = load_auth_settings(environ)
    registry_settings = load_registry_settings(environ)
    catalog_settings = load_catalog_settings(environ)
    if not catalog_settings.supplement_table_name:
        # 決裁 handler は補助テーブルなしではどの API も提供できないため、起動を止める
        raise ConfigurationError("決裁 handler の設定が不足しています: SUPPLEMENT_TABLE_NAME")
    region = registry_settings.region
    store = DraftStore(
        catalog_settings.supplement_table_name,
        registry_settings.arn,
        store_client or cast(DraftStoreClient, boto3.client("dynamodb", region_name=region)),
    )
    curation_client = registry_client or cast(
        RegistryStatusClient, boto3.client("agent-registry-control", region_name=region)
    )
    reads = CurationReads(registry_settings.arn, store, curation_client)
    curation = RecordCuration(reads, store, curation_client)
    reviews = ReviewQueue(reads)
    lifecycle = RecordLifecycle(reads)
    deprecation_requests = DeprecationRequests(reads, store)

    def list_reviews(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return reviews.list_reviews(caller, _query_of(event))

    def get_review(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("審査詳細にクエリパラメーターは指定できません。")
        return reviews.review(caller, _path_parameter(event))

    def get_lifecycle(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("公開管理の照会にクエリパラメーターは指定できません。")
        return lifecycle.inspect(caller, _path_parameter(event))

    def decline_deprecation(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return deprecation_requests.decline(
            caller, _path_parameter(event), _json_body(event, DEPRECATION_BODY_MAX_LENGTH)
        )

    # 回復は Registry の観測だけで下書きの操作を確定する。申請 API の権限と発見の設定は使わない
    recovery = OperationRecovery(
        store, curation, DraftSettlement(registry_settings.arn, store, curation_client)
    )

    def get_recovery(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("回復の照会にクエリパラメーターは指定できません。")
        return recovery.inspect(caller, _path_parameter(event))

    def recover_operation(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return recovery.recover(
            caller, _path_parameter(event), _json_body(event, RECOVERY_BODY_MAX_LENGTH)
        )

    def decision_route(kind: OperationKind) -> Route:
        """決裁の種別ごとの route。AWS の遷移先は種別だけで決まり、本文からは受け取らない。"""

        def decide(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
            return curation.decide(
                caller,
                _path_parameter(event),
                _json_body(event, DECISION_BODY_MAX_LENGTH),
                _idempotency_key(event),
                kind=kind,
            )

        return decide

    routes: Mapping[str, Route] = {
        "GET /api/reviews": list_reviews,
        "GET /api/reviews/{recordId}": get_review,
        "POST /api/reviews/{recordId}/approve": decision_route(APPROVE_KIND),
        "POST /api/reviews/{recordId}/reject": decision_route(REJECT_KIND),
        # 公開停止と廃止も決裁 handler が行う（UpdateRegistryRecordStatus は決裁 Lambda の権限）
        **{
            f"POST /api/records/{{recordId}}/{action}": decision_route(kind)
            for action, kind in LIFECYCLE_ACTIONS.items()
        },
        "GET /api/records/{recordId}/lifecycle": get_lifecycle,
        "POST /api/records/{recordId}/deprecation-requests/decline": decline_deprecation,
        "GET /api/records/{recordId}/recovery": get_recovery,
        "POST /api/records/{recordId}/recovery": recover_operation,
    }

    return _serve(routes, settings)
