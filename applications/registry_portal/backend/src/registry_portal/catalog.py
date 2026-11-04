"""カタログ（公開承認済みの Registry レコード）の API。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from registry_portal.domain.catalog import (
    CATALOG_RECORD_TYPES,
    CatalogItem,
    CatalogReference,
    CatalogSearch,
    is_catalog_record_type,
)
from registry_portal.errors import (
    CatalogItemNotFoundError,
    DiscoveryUnavailableError,
    InvalidPageTokenError,
    RequestValidationError,
)
from registry_portal.services.catalog_supplement import CatalogSupplement
from registry_portal.services.discovery import (
    DiscoveryClient,
    DiscoveryUnavailable,
    PageTokenRejected,
)

# 受け付けるクエリパラメーター。Registry ID や endpoint URL は受け取らない
_ALLOWED_QUERY_KEYS = frozenset({"type", "a2a", "nextToken"})
# AWS の nextToken の長さの上限は4096。空白・制御文字・非 ASCII を含むものは拒否する
_RECORD_ID_PATTERN = re.compile(r"[A-Za-z0-9]{12}")
_NEXT_TOKEN_PATTERN = re.compile(r"[\x21-\x7e]{1,4096}")
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
    if a2a is not None and (record_type != "AGENT" or a2a != "true"):
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


def search_catalog(
    discovery: DiscoveryClient, body: Mapping[str, Any]
) -> tuple[int, Mapping[str, Any]]:
    """検索条件を検証し、AWS のランキング順で最大20件を返す。"""
    if not set(body) <= {"query", "type", "a2a", "dataClassification"}:
        raise RequestValidationError("指定できない検索条件が含まれています。")
    query = body.get("query")
    if not isinstance(query, str) or len(query) > 256:
        raise RequestValidationError("検索語（query）は256文字以内で指定してください。")
    record_type = body.get("type")
    if "type" in body and not is_catalog_record_type(record_type):
        raise RequestValidationError(_INVALID_TYPE_MESSAGE)
    a2a = body.get("a2a")
    if "a2a" in body and (not isinstance(a2a, bool) or record_type not in (None, "AGENT")):
        raise RequestValidationError("A2A の指定は Agent の検索にのみ使用できます。")
    classification = body.get("dataClassification")
    if "dataClassification" in body and classification not in ("public", "private"):
        raise RequestValidationError("データ分類には public または private を指定してください。")
    query = query.strip()
    if not query and not (record_type or a2a is not None or classification):
        raise RequestValidationError("検索語または絞り込み条件を指定してください。")

    try:
        items = discovery.search_records(CatalogSearch(query, record_type, a2a, classification))
    except DiscoveryUnavailable:
        raise DiscoveryUnavailableError() from None
    return 200, {"items": [_item_dto(item) for item in items]}


def get_catalog_detail(
    discovery: DiscoveryClient, record_id: str, supplement: CatalogSupplement | None
) -> tuple[int, Mapping[str, Any]]:
    """発見 API からカタログ項目の公開中の版を返す。"""
    if not _RECORD_ID_PATTERN.fullmatch(record_id):
        raise RequestValidationError("カタログ項目の ID の形式が正しくありません。")
    try:
        detail = discovery.get_record(record_id)
    except DiscoveryUnavailable:
        raise DiscoveryUnavailableError() from None
    if detail is None:
        raise CatalogItemNotFoundError()
    owner_team = supplement.owner_team(detail.item.record_id) if supplement is not None else None
    related: tuple[CatalogReference, ...] | None = None
    if detail.item.record_type != "AGENT":
        related = ()
    elif supplement is not None:
        related = supplement.related_references(detail.item)
    return 200, {
        **_item_dto(detail.item),
        "definition": detail.definition,
        "ownerTeam": owner_team if owner_team is not None else detail.owner_team,
        "contact": detail.contact,
        "termsOfUse": detail.terms_of_use,
        "connectionReference": detail.connection_reference,
        "dataClassification": detail.data_classification,
        "skillMarkdown": detail.skill_markdown,
        "relatedReferences": (
            [{"recordId": ref.record_id, "recordVersion": ref.record_version} for ref in related]
            if related is not None
            else None
        ),
    }
