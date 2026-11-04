"""カタログ（公開承認済みの Registry レコード）の API。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from registry_portal.domain.catalog import (
    CATALOG_RECORD_TYPES,
    CatalogItem,
    is_catalog_record_type,
)
from registry_portal.errors import (
    DiscoveryUnavailableError,
    InvalidPageTokenError,
    RequestValidationError,
)
from registry_portal.services.discovery import (
    DiscoveryClient,
    DiscoveryUnavailable,
    PageTokenRejected,
)

# 受け付けるクエリパラメーター。Registry ID や endpoint URL は受け取らない
_ALLOWED_QUERY_KEYS = frozenset({"type", "nextToken"})
# AWS の nextToken の長さの上限は4096。空白・制御文字・非 ASCII を含むものは拒否する
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
    next_token = query.get("nextToken")
    if next_token is not None and _NEXT_TOKEN_PATTERN.fullmatch(next_token) is None:
        raise InvalidPageTokenError()

    try:
        page = discovery.list_records(record_type, next_token)
    except PageTokenRejected:
        raise InvalidPageTokenError() from None
    except DiscoveryUnavailable:
        raise DiscoveryUnavailableError() from None
    return 200, {"items": [_item_dto(item) for item in page.items], "nextToken": page.next_token}
