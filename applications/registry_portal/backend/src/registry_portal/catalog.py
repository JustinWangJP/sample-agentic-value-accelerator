"""カタログ（公開承認済みの Registry レコード）の API。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from registry_portal.domain.catalog import (
    CATALOG_RECORD_TYPES,
    DATA_CLASSIFICATIONS,
    CatalogItem,
    CatalogSearch,
    PublishedSnapshot,
    is_catalog_record_type,
    is_data_classification,
    supports_a2a_filter,
)
from registry_portal.domain.drafts import RECORD_ID_PATTERN
from registry_portal.errors import (
    A2ASearchNotReadyError,
    CatalogItemNotFoundError,
    DiscoveryUnavailableError,
    InvalidPageTokenError,
    RequestValidationError,
)
from registry_portal.services.catalog_supplement import (
    CatalogSupplement,
    CatalogSupplementUnavailable,
)
from registry_portal.services.discovery import (
    DiscoveryClient,
    DiscoveryUnavailable,
    PageTokenRejected,
)

# 一覧が受け付けるクエリパラメーター。Registry ID や endpoint URL は受け取らない
_ALLOWED_QUERY_KEYS = frozenset({"type", "a2a", "nextToken"})
# 検索が受け付ける本文のキー。AWS の filters や registryIds は受け取らない
_ALLOWED_SEARCH_KEYS = frozenset({"query", "type", "a2a", "dataClassification"})
# AWS の nextToken の長さの上限は4096。空白・制御文字・非 ASCII を含むものは拒否する
_NEXT_TOKEN_PATTERN = re.compile(r"[\x21-\x7e]{1,4096}")
# AWS の recordId は英数字12文字
# AWS の searchQuery は1〜256文字
_SEARCH_QUERY_MAX_LENGTH = 256
_INVALID_TYPE_MESSAGE = (
    f"種別（type）には {'、'.join(CATALOG_RECORD_TYPES)} のいずれかを指定してください。"
)


def _item_dto(item: CatalogItem) -> dict[str, Any]:
    return {
        "recordId": item.record_id,
        "name": item.name,
        "displayName": item.display_name,
        "description": item.description,
        "recordType": item.record_type,
        "descriptorTypes": list(item.descriptor_types),
        "isA2A": item.is_a2a,
        "recordVersion": item.record_version,
        "updatedAt": item.updated_at,
    }


def list_catalog(
    discovery: DiscoveryClient, query: Mapping[str, str]
) -> tuple[int, Mapping[str, Any]]:
    """種別を指定して、カタログ項目を1ページ分返す。"""
    if not set(query) <= _ALLOWED_QUERY_KEYS:
        raise RequestValidationError("指定できないパラメーターが含まれています。")
    record_type = query.get("type")
    if not is_catalog_record_type(record_type):
        raise RequestValidationError(_INVALID_TYPE_MESSAGE)
    a2a = query.get("a2a")
    if a2a is not None and (not supports_a2a_filter(record_type) or a2a != "true"):
        raise RequestValidationError("a2a=true は Agent の一覧にのみ指定できます。")
    next_token = query.get("nextToken")
    if next_token is not None and _NEXT_TOKEN_PATTERN.fullmatch(next_token) is None:
        raise InvalidPageTokenError()

    try:
        page = discovery.list_records(record_type, next_token, a2a_only=a2a == "true")
    except PageTokenRejected:
        raise InvalidPageTokenError() from None
    except DiscoveryUnavailable:
        raise DiscoveryUnavailableError() from None
    return 200, {"items": [_item_dto(item) for item in page.items], "nextToken": page.next_token}


def _search_of(body: Mapping[str, Any]) -> CatalogSearch:
    """本文を検証し、許可した条件だけから検索条件を組み立てる。"""
    if not set(body) <= _ALLOWED_SEARCH_KEYS:
        raise RequestValidationError("指定できない検索条件が含まれています。")
    query = body.get("query")
    query = query.strip() if isinstance(query, str) else ""
    if not 1 <= len(query) <= _SEARCH_QUERY_MAX_LENGTH:
        raise RequestValidationError(
            f"検索語（query）は1〜{_SEARCH_QUERY_MAX_LENGTH}文字で指定してください。"
            "条件だけで探す場合は一覧を使ってください。"
        )
    record_type = body.get("type")
    if record_type is not None and not is_catalog_record_type(record_type):
        raise RequestValidationError(_INVALID_TYPE_MESSAGE)
    a2a = body.get("a2a")
    if a2a is not None and (not isinstance(a2a, bool) or not supports_a2a_filter(record_type)):
        raise RequestValidationError("A2A の指定は Agent の検索にのみ使用できます。")
    classification = body.get("dataClassification")
    if classification is not None and not is_data_classification(classification):
        raise RequestValidationError(
            f"データ分類には {' または '.join(DATA_CLASSIFICATIONS)} を指定してください。"
        )
    return CatalogSearch(query, record_type, a2a, classification)


def search_catalog(
    discovery: DiscoveryClient, body: Mapping[str, Any], *, a2a_search_enabled: bool
) -> tuple[int, Mapping[str, Any]]:
    """検索条件を検証し、AWS のランキング順で最大20件を返す。"""
    search = _search_of(body)
    if search.a2a is not None and not a2a_search_enabled:
        raise A2ASearchNotReadyError()

    try:
        result = discovery.search_records(search)
    except DiscoveryUnavailable:
        raise DiscoveryUnavailableError() from None
    return 200, {
        "items": [_item_dto(item) for item in result.items],
        "inconsistentA2ACount": result.inconsistent_a2a_count,
    }


def get_catalog_detail(
    discovery: DiscoveryClient, record_id: str, supplement: CatalogSupplement | None
) -> tuple[int, Mapping[str, Any]]:
    """発見 API からカタログ項目の公開中の版を返す。"""
    if not RECORD_ID_PATTERN.fullmatch(record_id):
        raise RequestValidationError("カタログ項目の ID の形式が正しくありません。")
    try:
        detail = discovery.get_record(record_id)
    except DiscoveryUnavailable:
        raise DiscoveryUnavailableError() from None
    if detail is None:
        raise CatalogItemNotFoundError()

    owner_team: str | None = None
    snapshot: PublishedSnapshot | None = None
    if supplement is not None:
        try:
            owner_team = supplement.owner_team(detail.item.record_id)
            snapshot = supplement.published_snapshot(detail.item)
        except CatalogSupplementUnavailable:
            raise DiscoveryUnavailableError() from None
    return 200, {
        **_item_dto(detail.item),
        "definition": detail.definition,
        # 未管理レコードは、custom metadata の写しを表示用に使う
        "ownerTeam": owner_team if owner_team is not None else detail.owner_team,
        "dataClassification": detail.data_classification,
        "skillMarkdown": detail.skill_markdown,
        # 公開版スナップショットがなければ null（未取得）。値がない項目は空文字（未登録）
        "contact": snapshot.contact if snapshot else None,
        "termsOfUse": snapshot.terms_of_use if snapshot else None,
        "connectionReference": snapshot.connection_reference if snapshot else None,
        # 関連参照は Agent だけが持つ。Agent 以外は常に空
        "relatedReferences": (
            [
                {"recordId": ref.record_id, "recordVersion": ref.record_version}
                for ref in (snapshot.related_references if snapshot else ())
            ]
            if snapshot or detail.item.record_type != "AGENT"
            else None
        ),
    }
