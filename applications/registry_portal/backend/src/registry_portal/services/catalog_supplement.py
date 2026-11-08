"""補助テーブルから、カタログ項目の所有チームと公開中の版のスナップショットを読み取る。

失敗はこのモジュールの例外（CatalogSupplementUnavailable）で表す。
API の応答（HTTP ステータスと日本語メッセージ）への変換は呼び出し側で行う。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.domain.catalog import (
    A2A_RECORD_TYPE,
    CatalogItem,
    CatalogReference,
    PublishedSnapshot,
    published_snapshot_sk,
)

logger = logging.getLogger(__name__)


class SupplementClient(Protocol):
    def get_item(
        self, *, TableName: str, Key: dict[str, dict[str, str]], ConsistentRead: bool
    ) -> Mapping[str, Any]: ...


class CatalogSupplementUnavailable(Exception):
    """補助テーブルを読み取れない、または保存内容が契約に合わない。"""


class CatalogSupplement:
    """Registry が公開中の版を返したレコードの補助情報を読み取る。"""

    def __init__(
        self,
        table_name: str,
        registry_arn: str,
        region: str,
        client: SupplementClient | None = None,
    ) -> None:
        self._table_name = table_name
        self._registry_arn = registry_arn
        self._client = client or cast(
            SupplementClient, boto3.client("dynamodb", region_name=region)
        )

    def owner_team(self, record_id: str) -> str | None:
        """所有チームの正本を返す。未管理レコード（管理情報がない、または管理対象外）なら None。"""
        stored = self._get_item(record_id, "MANAGEMENT")
        if stored is None or _attribute(stored, "managed", "BOOL") is False:
            return None
        owner_team = _attribute(stored, "ownerTeam", "S")
        if not isinstance(owner_team, str) or not owner_team:
            raise _unavailable("管理情報に所有チームがありません")
        return owner_team

    def published_snapshot(self, item: CatalogItem) -> PublishedSnapshot | None:
        """公開中の版のスナップショットを返す。保存されていなければ None。"""
        stored = self._get_item(item.record_id, published_snapshot_sk(item.record_version))
        if stored is None:
            return None
        related = (
            _references(_attribute(stored, "relatedReferences", "L"))
            if item.record_type == A2A_RECORD_TYPE
            else ()
        )
        return PublishedSnapshot(
            contact=_optional_string(stored, "contact"),
            terms_of_use=_optional_string(stored, "termsOfUse"),
            connection_reference=_optional_string(stored, "connectionReference"),
            related_references=related,
        )

    def _get_item(self, record_id: str, sort_key: str) -> object:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": f"REGISTRY#{self._registry_arn}#RECORD#{record_id}"},
                    "sk": {"S": sort_key},
                },
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as error:
            raise _unavailable(
                "カタログ補助情報の取得に失敗しました: error=%s", type(error).__name__
            ) from None
        return response.get("Item")


def _unavailable(message: str, *args: object) -> CatalogSupplementUnavailable:
    """失敗をログに残し、送出する例外を返す。"""
    logger.warning(message, *args)
    return CatalogSupplementUnavailable()


def _attribute(item: object, name: str, value_type: str) -> object:
    value = item.get(name) if isinstance(item, Mapping) else None
    return value.get(value_type) if isinstance(value, Mapping) else None


def _optional_string(item: object, name: str) -> str:
    """文字列（S）の属性。ない場合は空文字、文字列以外なら契約違反とする。"""
    if not isinstance(item, Mapping) or name not in item:
        return ""
    value = _attribute(item, name, "S")
    if not isinstance(value, str):
        raise _unavailable("公開版スナップショットの %s が文字列ではありません", name)
    return value


def _references(references: object) -> tuple[CatalogReference, ...]:
    if not isinstance(references, list):
        raise _unavailable("公開版スナップショットに関連参照のリストがありません")
    parsed = []
    for attribute in references:
        reference = attribute.get("M") if isinstance(attribute, Mapping) else None
        record_id = _attribute(reference, "recordId", "S")
        record_version = _attribute(reference, "recordVersion", "S")
        if not (isinstance(record_id, str) and record_id):
            raise _unavailable("関連参照の recordId を読み取れません")
        if not (isinstance(record_version, str) and record_version):
            raise _unavailable("関連参照の recordVersion を読み取れません")
        parsed.append(CatalogReference(record_id, record_version))
    return tuple(parsed)
