"""API Gateway REST API の Lambda プロキシ統合用 handler を組み立てる。"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple, cast

import boto3

from registry_portal.administration import (
    AdministrationReads,
    RegistryAdministration,
    RegistryAdministrationClient,
)
from registry_portal.auth import Caller, authorize
from registry_portal.catalog import get_catalog_detail, list_catalog, search_catalog
from registry_portal.config import (
    AuthSettings,
    ConfigurationError,
    load_auth_settings,
    load_catalog_settings,
    load_cors_origins,
    load_discovery_settings,
    load_registry_settings,
)
from registry_portal.curation import DECISION_BODY_MAX_LENGTH, RecordCuration
from registry_portal.curation_reads import CurationReads, RegistryReadClient, RegistryStatusClient
from registry_portal.deprecation import (
    DEPRECATION_BODY_MAX_LENGTH,
    DeprecationRequests,
)
from registry_portal.domain.curation import PUBLICATION_ACTIONS
from registry_portal.domain.drafts import (
    ADOPT_KIND,
    APPROVE_KIND,
    REASSIGN_OWNER_KIND,
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
from registry_portal.notification_events import RegistryEventNotifier
from registry_portal.notifications import (
    READ_BODY_MAX_LENGTH,
    NotificationInbox,
    NotificationsNotConfigured,
)
from registry_portal.publication import PublicationSearch, RecordPublication
from registry_portal.recovery import RECOVERY_BODY_MAX_LENGTH, OperationRecovery
from registry_portal.review_queue import ReviewQueue
from registry_portal.services.catalog_supplement import CatalogSupplement, SupplementClient
from registry_portal.services.discovery import DiscoveryDependencies, create_discovery_client
from registry_portal.services.notification_store import NotificationStore, NotificationStoreClient
from registry_portal.services.reference_index import ReferenceIndex
from registry_portal.services.supplement_store import SupplementStore, SupplementStoreClient

logger = logging.getLogger(__name__)

LambdaEvent = Mapping[str, Any]
LambdaResponse = dict[str, Any]
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


class _ApiRequest(NamedTuple):
    """REST API の経路照合と認可に使う要求。"""

    method: str
    # ルート表と照合するキー（例: GET /api/me）
    route_key: str
    # 検証済みの claims。トークンがない、または想定外の型なら None
    claims: Mapping[str, Any] | None


def _api_request(event: LambdaEvent) -> _ApiRequest:
    """ステージ名を含まないパスと Cognito authorizer の検証済み claims を取り出す。"""
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    authorizer = _mapping_or_empty(_request_context(event).get("authorizer"))
    claims = authorizer.get("claims")
    return _ApiRequest(method, f"{method} {path}", claims if isinstance(claims, Mapping) else None)


# ルート表のキーに含まれる path パラメーター（例: {recordId}）。1つのルートに1つまで
_PATH_PARAMETER = re.compile(r"\{([A-Za-z]+)\}")


def _route_template(key: str, templates: Iterable[str]) -> tuple[str, dict[str, str]]:
    """ルート表のキーと path パラメーター（なければ空）を返す。

    パラメーターを含むルートは、ルート表のキー（例: POST /api/records/{recordId}/submit）そのものと
    照合する。ルートを足すときにルート表とは別の一覧を揃えなくて済むようにするため。
    """
    for template in templates:
        match = _PATH_PARAMETER.search(template)
        if match is None:
            continue
        prefix, suffix = template[: match.start()], template[match.end() :]
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        parameter = key[len(prefix) : len(key) - len(suffix)]
        if parameter and "/" not in parameter:
            return template, {match.group(1): parameter}
    return key, {}


def _path_parameter(event: LambdaEvent) -> str:
    """ルート表との照合で得た path パラメーターの値（ApiHandler._dispatch が pathParameters に設定する）。"""
    parameters = _mapping_or_empty(event.get("pathParameters"))
    return str(next(iter(parameters.values()), ""))


def _query_of(event: LambdaEvent) -> Mapping[str, str]:
    """単一値のクエリパラメーターを取り出す。

    REST API の Lambda プロキシ統合では、同じ名前が複数あると queryStringParameters には最後の値だけが入り、
    すべての値は multiValueQueryStringParameters に入る（公式文書の入力例で、同じ名前の parameter2 が
    queryStringParameters では "value2"、multiValueQueryStringParameters では ["value1", "value2"] になる）。
    値の中のカンマは区切りにならない（parameter3 は "value1,value2" のまま）。ここでは前者だけを使うので、
    同じ名前を複数送ると最後の値が採用される。SPA は同じ名前のキーを送らない。
    https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html
    """
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


def _header(event: LambdaEvent, name: str) -> Any:
    """リクエストヘッダーの値。ヘッダー名の大文字・小文字は区別しない。なければ None。"""
    headers = _mapping_or_empty(event.get("headers"))
    wanted = name.lower()
    return next((value for key, value in headers.items() if key.lower() == wanted), None)


def _idempotency_key(event: LambdaEvent) -> str:
    """Idempotency-Key ヘッダーの値。なければ空文字。"""
    value = _header(event, "Idempotency-Key")
    return "" if value is None else str(value)


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


# プリフライトで許可するメソッド。プリフライトは管理 Lambda がすべてのリソースについて答えるため、
# 決裁 Lambda の経路のメソッドも含める。経路表（routes.json）のメソッドがすべて含まれることは
# tests/test_route_tables.py で確かめる
CORS_ALLOWED_METHODS = "GET, POST, PATCH, OPTIONS"
# プリフライトで許可するリクエストヘッダー。SPA が送るヘッダーだけを許可する。
# OpenAPI 仕様（openapi.yaml の CORS）と同じ値であることは tests/test_route_tables.py で確かめる
CORS_ALLOWED_HEADERS = "Authorization, Content-Type, Idempotency-Key"
# プリフライトの結果をブラウザが再利用してよい秒数
CORS_MAX_AGE_SECONDS = "300"


class CorsPolicy:
    """REST API 形式の応答に付ける CORS ヘッダーを決める。

    REST API の Lambda プロキシ統合では統合応答を設定できないため、CORS ヘッダーは Lambda が返す
    （根拠の公式文書は README「カタログの一覧・検索・詳細」の CORS の説明にある）。Origin を許可リストと照合し、一致したときだけ
    Access-Control-Allow-Origin を付ける。answers_preflight が真なら、OPTIONS をルート表に渡さず
    204 で応答する（管理 Lambda だけが真）。
    """

    def __init__(self, origins: frozenset[str], *, answers_preflight: bool) -> None:
        self._origins = origins
        self._answers_preflight = answers_preflight

    def headers(self, event: LambdaEvent) -> dict[str, str]:
        """通常の応答に付けるヘッダー。許可していない Origin には Vary だけを返す。"""
        headers = {"Vary": "Origin"}
        origin = _header(event, "Origin")
        if isinstance(origin, str) and origin in self._origins:
            headers["Access-Control-Allow-Origin"] = origin
        return headers

    def preflight(self, event: LambdaEvent, method: str) -> LambdaResponse | None:
        """この policy が答えるプリフライトなら 204 応答を返す。それ以外は None（ルート表で処理する）。"""
        if not self._answers_preflight or method != "OPTIONS":
            return None
        return self._preflight_response(event)

    def _preflight_response(self, event: LambdaEvent) -> LambdaResponse:
        """プリフライトへの 204 応答。許可した Origin にだけメソッド・ヘッダー・有効期間を返す。"""
        headers = self.headers(event)
        if "Access-Control-Allow-Origin" in headers:
            headers.update(
                {
                    "Access-Control-Allow-Methods": CORS_ALLOWED_METHODS,
                    "Access-Control-Allow-Headers": CORS_ALLOWED_HEADERS,
                    "Access-Control-Max-Age": CORS_MAX_AGE_SECONDS,
                }
            )
        return {"statusCode": 204, "headers": headers, "body": ""}


class ApiHandler:
    """ルート表で要求を処理する Lambda handler。認可・エラー応答はすべての API で共通。

    ルート表は経路のキー（例: GET /api/me）から処理する関数への辞書で、Lambda 側の定義元。
    API Gateway 側の定義元である経路表（routes.json）とは別物で、両者の一致は
    tests/test_route_tables.py で確かめる（用語は README の「経路」「経路表」「ルート表」）。
    REST API 形式の応答には CorsPolicy が決めた CORS ヘッダーを付ける。
    """

    def __init__(
        self,
        routes: Mapping[str, Route],
        settings: AuthSettings,
        cors: CorsPolicy,
    ) -> None:
        self._routes = dict(routes)
        self._settings = settings
        self._cors = cors

    @property
    def route_keys(self) -> frozenset[str]:
        """ルート表のキー（例: GET /api/records/{recordId}）。

        ルート表は create_*_handler の中で依存を閉じ込めて組み立てるため、経路表との一致を
        確かめる（tests/test_route_tables.py）には handler からキーを取り出すしかない。
        """
        return frozenset(self._routes)

    def __call__(self, event: LambdaEvent, context: Any) -> LambdaResponse:
        request = _api_request(event)
        preflight = self._cors.preflight(event, request.method)
        if preflight is not None:
            return preflight
        response = self._dispatch(event, request)
        response["headers"].update(self._cors.headers(event))
        return response

    def _dispatch(self, event: LambdaEvent, request: _ApiRequest) -> LambdaResponse:
        request_id = _request_context(event).get("requestId")
        try:
            caller = authorize(request.claims, self._settings)
            template, parameters = _route_template(request.route_key, self._routes)
            route = self._routes.get(template)
            if route is None:
                raise RouteNotFoundError()
            # path パラメーターは実際の path とルート表の照合で決め、受け取った値には頼らない
            status, body = route(caller, {**event, "pathParameters": parameters})
            return _json_response(status, body)
        except ApiError as error:
            # ログにはトークンや claims の全文を出さない
            logger.info("API エラー: code=%s request_id=%s", error.code, request_id)
            return _json_response(error.status, error.to_dto(request_id))
        except Exception:
            logger.exception("想定外のエラー: request_id=%s", request_id)
            return _json_response(500, ApiError().to_dto(request_id))


class _SupplementStores(NamedTuple):
    """同じ補助テーブルを、同じ DynamoDB クライアントで読み書きするストア。"""

    records: SupplementStore
    notifications: NotificationStore
    references: ReferenceIndex


def _supplement_stores(
    table: str, registry_arn: str, region: str, store_client: SupplementStoreClient | None
) -> _SupplementStores:
    """補助テーブルのストアを作る。store_client を渡さなければ DynamoDB クライアントを作る（通信はしない）。"""
    client = store_client or cast(
        SupplementStoreClient, boto3.client("dynamodb", region_name=region)
    )
    return _SupplementStores(
        SupplementStore(table, registry_arn, client),
        NotificationStore(table, registry_arn, cast(NotificationStoreClient, client)),
        ReferenceIndex(table, registry_arn, client),
    )


def create_management_handler(
    environ: Mapping[str, str],
    *,
    discovery: DiscoveryDependencies | None = None,
    supplement_client: SupplementClient | None = None,
    registry_client: RegistryControlClient | None = None,
    supplement_store_client: SupplementStoreClient | None = None,
) -> ApiHandler:
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
    reads = None
    # 決裁操作の照会（GET /api/operations）は管理 handler が担う。決裁自体は決裁 handler が行うため、
    # このインスタンスは AWS の状態更新 API を呼び出さない（IAM でも拒否される）
    curation = None
    history_service = None
    deprecation_requests = None
    inbox = None
    if catalog_settings.supplement_table_name:
        control_client = registry_client or cast(
            RegistryControlClient,
            boto3.client("agent-registry-control", region_name=region),
        )
        stores = _supplement_stores(
            catalog_settings.supplement_table_name,
            discovery_settings.registry_arn,
            region,
            supplement_store_client,
        )
        store = stores.records
        inbox = NotificationInbox(stores.notifications)
        drafts = RecordDrafts(
            discovery_settings.registry_arn, store, control_client, discovery_client
        )
        # 管理 handler の Registry クライアントは状態更新 API を持たない（IAM でも拒否される）。
        # 操作照会は AWS を呼び直さないため、型の上だけ決裁 handler と揃える
        status_client = cast(RegistryStatusClient, control_client)
        reads = CurationReads(discovery_settings.registry_arn, store, status_client)
        curation = RecordCuration(reads, store, status_client)
        history_service = RecordHistory(store)
        deprecation_requests = DeprecationRequests(reads, store, stores.notifications)

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

    # 通知は宛先グループに属する主体（Curator・Admin・所属チームの Publisher）が読む。
    # 宛先グループの判定は通知 API の内側で行い、Viewer には0件を返す
    def inbox_for() -> NotificationInbox:
        if inbox is None:
            raise NotificationsNotConfigured()
        return inbox

    def get_notifications(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return inbox_for().list_notifications(caller, _query_of(event))

    def read_notifications(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return inbox_for().mark_read(caller, _json_body(event, READ_BODY_MAX_LENGTH))

    admin_reads = AdministrationReads(
        load_registry_settings(environ),
        cast(
            RegistryAdministrationClient,
            registry_client or boto3.client("agent-registry-control", region_name=region),
        ),
        reads,
    )

    def admin_list(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return admin_reads.list_records(caller, _query_of(event))

    def admin_connection(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return admin_reads.connection(caller, _query_of(event))

    routes: Mapping[str, Route] = {
        **MANAGEMENT_ROUTES,
        "GET /api/admin/records": admin_list,
        "GET /api/admin/connection": admin_connection,
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
        "GET /api/notifications": get_notifications,
        "POST /api/notifications/read": read_notifications,
    }

    return ApiHandler(
        routes, settings, CorsPolicy(load_cors_origins(environ), answers_preflight=True)
    )


def create_curation_handler(
    environ: Mapping[str, str],
    *,
    registry_client: RegistryStatusClient | None = None,
    store_client: SupplementStoreClient | None = None,
    discovery: DiscoveryDependencies | None = None,
) -> ApiHandler:
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
    stores = _supplement_stores(
        catalog_settings.supplement_table_name, registry_settings.arn, region, store_client
    )
    store = stores.records
    curation_client = registry_client or cast(
        RegistryStatusClient, boto3.client("agent-registry-control", region_name=region)
    )
    reads = CurationReads(registry_settings.arn, store, curation_client)
    curation = RecordCuration(reads, store, curation_client)
    reviews = ReviewQueue(reads)
    publication = RecordPublication(reads)
    publication_search = PublicationSearch(reads)
    deprecation_requests = DeprecationRequests(reads, store, stores.notifications)

    def list_reviews(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return reviews.list_reviews(caller, _query_of(event))

    def get_review(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("審査詳細にクエリパラメーターは指定できません。")
        return reviews.review(caller, _path_parameter(event))

    def get_publication(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        if _query_of(event):
            raise RequestValidationError("公開管理の照会にクエリパラメーターは指定できません。")
        return publication.inspect(caller, _path_parameter(event))

    def search_publications(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
        return publication_search.search(caller, _query_of(event))

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

    discovery_client = (
        create_discovery_client(
            load_discovery_settings(environ), discovery or DiscoveryDependencies()
        )
        if environ.get("DISCOVERY_CLIENT_ID")
        else None
    )
    administration = RegistryAdministration(reads, store, discovery_client)

    def ownership_route(kind: OperationKind) -> Route:
        """取り込み・所有変更の route。操作の種類は route で決まり、本文からは受け取らない。"""

        def change(caller: Caller, event: LambdaEvent) -> tuple[int, Mapping[str, Any]]:
            return administration.change_ownership(
                caller,
                _path_parameter(event),
                _json_body(event),
                _idempotency_key(event),
                kind=kind,
            )

        return change

    routes: Mapping[str, Route] = {
        "POST /api/records/{recordId}/adopt": ownership_route(ADOPT_KIND),
        "POST /api/records/{recordId}/reassign-owner": ownership_route(REASSIGN_OWNER_KIND),
        "GET /api/reviews": list_reviews,
        "GET /api/reviews/{recordId}": get_review,
        "POST /api/reviews/{recordId}/approve": decision_route(APPROVE_KIND),
        "POST /api/reviews/{recordId}/reject": decision_route(REJECT_KIND),
        # 公開停止と廃止も決裁 handler が行う（UpdateRegistryRecordStatus は決裁 Lambda の権限）
        **{
            f"POST /api/records/{{recordId}}/{action}": decision_route(kind)
            for action, kind in PUBLICATION_ACTIONS.items()
        },
        "GET /api/records/{recordId}/publication": get_publication,
        # 管理レコードの検索（Curator）。_route_template に静的な route を優先する実装はなく、
        # 先頭一致で {recordId} を捕まえ得る。決裁 handler のルート表には
        # GET /api/records/{recordId}/publication しかないため、GET /api/records/publication が
        # 空の {recordId} として捕まえられることはなく、静的なキーとして直接照合される。
        # API Gateway 側では静的な route が parameterized route に優先する（README「IAM と route」）
        "GET /api/records/publication": search_publications,
        "POST /api/records/{recordId}/deprecation-requests/decline": decline_deprecation,
        "GET /api/records/{recordId}/recovery": get_recovery,
        "POST /api/records/{recordId}/recovery": recover_operation,
    }

    return ApiHandler(
        routes, settings, CorsPolicy(load_cors_origins(environ), answers_preflight=False)
    )


def create_event_handler(
    environ: Mapping[str, str],
    *,
    registry_client: RegistryReadClient | None = None,
    store_client: SupplementStoreClient | None = None,
) -> Callable[[Any, Any], dict[str, Any]]:
    """イベント処理 handler を作る。Registry の ARN と補助テーブルが不足していれば起動エラーにする。

    EventBridge のルールから Registry のレコードのイベントを受け取り、Registry を再取得して
    補助テーブルに通知を書く。認証設定は使わない（API Gateway からは呼ばれない）。
    Registry・補助テーブルの一時的な失敗は例外のまま返し、Lambda の非同期呼び出しの再試行に任せる。
    """
    registry_settings = load_registry_settings(environ)
    catalog_settings = load_catalog_settings(environ)
    if not catalog_settings.supplement_table_name:
        raise ConfigurationError(
            "イベント処理 handler の設定が不足しています: SUPPLEMENT_TABLE_NAME"
        )
    region = registry_settings.region
    stores = _supplement_stores(
        catalog_settings.supplement_table_name, registry_settings.arn, region, store_client
    )
    notifier = RegistryEventNotifier(
        registry_settings.arn,
        stores.records,
        stores.notifications,
        stores.references,
        registry_client
        or cast(RegistryReadClient, boto3.client("agent-registry-control", region_name=region)),
    )

    def handler(event: Any, context: Any) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            logger.warning("想定外の形のイベントを無視しました")
            return {"result": "IGNORED", "notified": 0}
        return notifier.handle(event)

    return handler
