"""カタログの所有チームと公開版の関連参照を補助テーブルから読み取る。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.config import DiscoverySettings
from registry_portal.domain.catalog import CatalogItem, CatalogReference
from registry_portal.errors import DiscoveryUnavailableError

logger = logging.getLogger(__name__)


class SupplementClient(Protocol):
    def get_item(
        self, *, TableName: str, Key: dict[str, dict[str, str]], ConsistentRead: bool
    ) -> Mapping[str, Any]: ...


class CatalogSupplement:
    """Registry が公開中の版を返したレコードの補助情報を読み取る。"""

    def __init__(
        self, table_name: str, settings: DiscoverySettings, client: SupplementClient | None = None
    ) -> None:
        self._table_name = table_name
        self._registry_arn = settings.registry_arn
        self._client = client or cast(
            SupplementClient, boto3.client("dynamodb", region_name=settings.registry_region)
        )

    def owner_team(self, record_id: str) -> str | None:
        stored = self._get_item(record_id, "MANAGEMENT")
        if stored is None or _attribute(stored, "managed", "BOOL") is False:
            return None
        owner_team = _attribute(stored, "ownerTeam", "S")
        if not isinstance(owner_team, str) or not owner_team:
            raise DiscoveryUnavailableError()
        return owner_team

    def related_references(self, item: CatalogItem) -> tuple[CatalogReference, ...] | None:
        stored = self._get_item(item.record_id, f"PUBLISHED#{item.record_version}")
        if stored is None:
            return None

        references = _attribute(stored, "relatedReferences", "L")
        if not isinstance(references, list):
            raise DiscoveryUnavailableError()
        parsed = []
        for attribute in references:
            reference = attribute.get("M") if isinstance(attribute, Mapping) else None
            record_id = _attribute(reference, "recordId", "S")
            record_version = _attribute(reference, "recordVersion", "S")
            if not isinstance(record_id, str) or not record_id:
                raise DiscoveryUnavailableError()
            if not isinstance(record_version, str) or not record_version:
                raise DiscoveryUnavailableError()
            parsed.append(CatalogReference(record_id, record_version))
        return tuple(parsed)

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
            logger.warning("カタログ補助情報の取得に失敗しました: error=%s", type(error).__name__)
            raise DiscoveryUnavailableError() from None
        return response.get("Item")


def _attribute(item: object, name: str, value_type: str) -> object:
    value = item.get(name) if isinstance(item, Mapping) else None
    return value.get(value_type) if isinstance(value, Mapping) else None
