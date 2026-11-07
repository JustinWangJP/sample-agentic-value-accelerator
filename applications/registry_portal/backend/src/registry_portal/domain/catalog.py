"""カタログ項目（公開承認済みの Registry レコードの版）のモデル。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard, get_args

# 画面の入口に対応する AWS の recordType。A2A は Agent の descriptor として扱う。
# SPA の `CatalogRecordType`（frontend/src/api/recordType.ts）と同じ値に揃える
CatalogRecordType = Literal["AGENT", "MCP", "SKILL"]
CATALOG_RECORD_TYPES: tuple[CatalogRecordType, ...] = get_args(CatalogRecordType)

# データ分類（custom metadata の dataClassification）。可視性は制御しない。
# SPA の `DataClassification`（frontend/src/api/dataClassification.ts）と同じ値に揃える
DataClassification = Literal["public", "private"]
DATA_CLASSIFICATIONS: tuple[DataClassification, ...] = get_args(DataClassification)

AGENT_RECORD_TYPE: CatalogRecordType = "AGENT"

# A2A は Agent の descriptor（Agent Card）なので、A2A の絞り込みは Agent にだけ意味を持つ
A2A_RECORD_TYPE: CatalogRecordType = AGENT_RECORD_TYPE

# primary descriptor の種類（AWS の descriptors のキー）。値はここを唯一の定義とし、
# SPA の `DESCRIPTOR_TYPES`（frontend/src/drafts/descriptors.ts）と同じ値に揃える
DescriptorType = Literal["custom", "a2aAgentCard", "mcpServer", "agentSkillsDefinition"]
DESCRIPTOR_TYPES: tuple[DescriptorType, ...] = get_args(DescriptorType)
CUSTOM_DESCRIPTOR_TYPE: DescriptorType = "custom"
A2A_DESCRIPTOR_TYPE: DescriptorType = "a2aAgentCard"
MCP_DESCRIPTOR_TYPE: DescriptorType = "mcpServer"
SKILL_DESCRIPTOR_TYPE: DescriptorType = "agentSkillsDefinition"


def is_catalog_record_type(value: object) -> TypeGuard[CatalogRecordType]:
    return value in CATALOG_RECORD_TYPES


def is_data_classification(value: object) -> TypeGuard[DataClassification]:
    return value in DATA_CLASSIFICATIONS


def supports_a2a_filter(record_type: CatalogRecordType | None) -> bool:
    """A2A の絞り込みを指定できる種別か。種別を指定しない検索（None）も Agent に絞って使える。"""
    return record_type in (None, A2A_RECORD_TYPE)


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
        return self.record_type == A2A_RECORD_TYPE and A2A_DESCRIPTOR_TYPE in self.descriptor_types


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[CatalogItem, ...]
    # 次のページがなければ None。AWS の nextToken をそのまま中継する
    next_token: str | None


@dataclass(frozen=True)
class CatalogSearch:
    """AWS のランキング検索に渡す検索語と、ポータルが許可する絞り込み。"""

    # 1〜256文字（AWS の searchQuery の制約）
    query: str
    record_type: CatalogRecordType | None
    a2a: bool | None
    data_classification: DataClassification | None

    @property
    def record_type_filter(self) -> CatalogRecordType | None:
        """AWS に渡す種別。A2A を指定したら Agent に絞る。None なら全種別。"""
        return A2A_RECORD_TYPE if self.a2a is not None else self.record_type


@dataclass(frozen=True)
class CatalogSearchResult:
    """AWS の順位を保った検索結果。"""

    items: tuple[CatalogItem, ...]
    # A2A 検索用 metadata（isA2A）と descriptor が食い違うため除外した件数
    inconsistent_a2a_count: int


@dataclass(frozen=True)
class CatalogDetail:
    """公開承認済みの版の定義と、Registry レコードから読む表示用メタデータ。"""

    item: CatalogItem
    definition: Mapping[str, Any]
    # custom metadata の検索用の写し。正本は補助テーブルの管理情報
    owner_team: str
    data_classification: DataClassification | None
    skill_markdown: str


@dataclass(frozen=True)
class CatalogReference:
    """公開中の Agent が参照するカタログ項目とその版。"""

    record_id: str
    record_version: str


@dataclass(frozen=True)
class PublishedSnapshot:
    """補助テーブルに保存した、公開中の版の公開内容のスナップショット。

    連絡先・利用条件・接続先の参照は検索や絞り込みに使わないため、custom metadata ではなく
    ここを正本とする（設計書「共通項目と custom metadata」「補助テーブルに保存するもの」）。
    値がない項目は空文字にする。
    """

    contact: str
    terms_of_use: str
    connection_reference: str
    # Agent 以外は常に空
    related_references: tuple[CatalogReference, ...]
