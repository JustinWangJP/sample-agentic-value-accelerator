"""関連参照の逆引き（参照先から、それを公開中の版で使う Agent を引く索引）の読み書き（#15）。

参照先の Skill・MCP サーバーが公開停止・廃止されたとき、その参照先を使う Agent の所有チームに
通知するために使う。公開承認の確定（補助ストアの finish_decision）と同じトランザクションで書き、
イベント処理（notification_events.py）が読む。キーの組み立てはこのクラスに閉じ込める。

- 逆引き: `pk` = `REGISTRY#{registryArn}#REFERENCED#{参照先の record ID}`、`sk` = `{Agent の record ID}`

新しい版で参照をやめても項目は消さない。読む側で、公開中の版の関連参照を読み直して確かめる。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, Unpack

from registry_portal.domain.catalog import related_reference_ids
from registry_portal.services.dynamodb import (
    QueryParams,
    TransactWriteItem,
    deserialize,
    put_item,
)


class ReferenceIndexClient(Protocol):
    """逆引きの読み取りが使う DynamoDB クライアントの操作。"""

    def query(self, **kwargs: Unpack[QueryParams]) -> Mapping[str, Any]: ...


class ReferenceIndex:
    """関連参照の逆引きを補助テーブルに保存し、参照先から Agent を引く。"""

    def __init__(self, table: str, registry: str, client: ReferenceIndexClient) -> None:
        self.table = table
        self.registry = registry
        self._client = client

    def _pk(self, target_id: str) -> str:
        return f"REGISTRY#{self.registry}#REFERENCED#{target_id}"

    def puts(self, record_id: str, published: Mapping[str, Any]) -> list[TransactWriteItem]:
        """公開承認した版（published）の関連参照を、参照先からの逆引きに書く項目。

        同じ内容を書き直すだけなので、条件なしで上書きする。
        """
        return [
            put_item(
                self.table,
                {"pk": self._pk(target), "sk": record_id, "recordId": record_id},
                None,
            )
            for target in sorted(related_reference_ids(published))
        ]

    def referencing_records(self, target_id: str) -> list[str]:
        """参照先（target_id）を関連参照に持つ版を公開承認した Agent の ID。形式が崩れていれば ValueError。"""
        params: QueryParams = {
            "TableName": self.table,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": {"S": self._pk(target_id)}},
        }
        records: list[str] = []
        while True:
            response = self._client.query(**params)
            for raw in response.get("Items", []):
                record_id = deserialize(raw).get("sk")
                if not isinstance(record_id, str):
                    raise ValueError("保存された関連参照の逆引きの形式が正しくありません。")
                records.append(record_id)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return records
            params["ExclusiveStartKey"] = last_key
