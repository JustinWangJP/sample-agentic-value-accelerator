"""カタログ項目（公開承認済みの Registry レコードの版）のモデル。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard, get_args

# 画面の入口に対応する AWS の recordType。A2A は Agent の descriptor として扱う。
# SPA の `CatalogRecordType`（frontend/src/api/client.ts）と同じ値に揃える
CatalogRecordType = Literal["AGENT", "MCP", "SKILL"]
CATALOG_RECORD_TYPES: tuple[CatalogRecordType, ...] = get_args(CatalogRecordType)


def is_catalog_record_type(value: object) -> TypeGuard[CatalogRecordType]:
    return value in CATALOG_RECORD_TYPES


@dataclass(frozen=True)
class CatalogItem:
    """カタログ項目の要約。"""

    record_id: str
    name: str
    display_name: str
    description: str
    record_type: CatalogRecordType
    descriptor_types: tuple[str, ...]
    record_version: str
    updated_at: str

    @property
    def is_a2a(self) -> bool:
        return self.record_type == "AGENT" and "a2aAgentCard" in self.descriptor_types


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[CatalogItem, ...]
    # 次のページがなければ None。AWS の nextToken をそのまま中継する
    next_token: str | None


@dataclass(frozen=True)
class CatalogSearch:
    """AWS のランキング検索に渡す検索語と、ポータルが許可する絞り込み。"""

    query: str
    record_type: CatalogRecordType | None
    a2a: bool | None
    data_classification: Literal["public", "private"] | None


@dataclass(frozen=True)
class CatalogDetail:
    """公開承認済みの版の定義と、詳細画面で表示するメタデータ。"""

    item: CatalogItem
    definition: Mapping[str, Any]
    owner_team: str
    contact: str
    terms_of_use: str
    connection_reference: str
    data_classification: str
    skill_markdown: str


@dataclass(frozen=True)
class CatalogReference:
    """公開中の Agent が参照するカタログ項目とその版。"""

    record_id: str
    record_version: str
