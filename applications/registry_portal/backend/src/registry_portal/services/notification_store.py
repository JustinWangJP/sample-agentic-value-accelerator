"""補助テーブルの通知・重複除去の印・既読位置の読み書き（#15）。

キーの組み立てはこのクラスに閉じ込める。項目はすべて90日の TTL（`expiresAt`）を持つ。

- 通知: `pk` = `REGISTRY#{registryArn}#NOTIFICATION#{宛先グループ}`、`sk` = `{時刻}#{通知 ID}`（既読位置）
- 重複除去の印: `pk` = `REGISTRY#{registryArn}#NOTIFIED#{出来事のキー}`、`sk` = `{宛先グループ}#{種別}`
- 処理済みのイベント: `pk` = `REGISTRY#{registryArn}#EVENT#{event ID}`、`sk` = `EVENT`
- 既読位置: `pk` = `REGISTRY#{registryArn}#READ#{issuer}#{subject}`、`sk` = `READ`
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from registry_portal.domain.drafts import PrincipalRecord
from registry_portal.domain.notifications import NOTIFICATION_RETENTION_SECONDS, Notification
from registry_portal.services.supplement_store import TTL_ATTRIBUTE as _TTL_ATTRIBUTE


class NotificationStoreClient(Protocol):
    """通知の補助ストアが使う DynamoDB クライアントの操作。"""

    def get_item(
        self, *, TableName: str, Key: dict[str, dict[str, str]], ConsistentRead: bool
    ) -> Mapping[str, Any]: ...

    def transact_write_items(self, *, TransactItems: list[dict[str, Any]]) -> Mapping[str, Any]: ...

    def query(self, **kwargs: Any) -> Mapping[str, Any]: ...


def _serialize(item: Mapping[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in item.items()}


def _deserialize(item: Mapping[str, Any]) -> dict[str, Any]:
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in item.items()}


def _condition_failed(error: ClientError) -> bool:
    """トランザクションが条件式（重複・既読位置の後退）で取り消されたか。"""
    reasons = error.response.get("CancellationReasons", [])
    return error.response["Error"]["Code"] == "TransactionCanceledException" and any(
        reason.get("Code") == "ConditionalCheckFailed" for reason in reasons
    )


class NotificationStore:
    """通知と既読位置を補助テーブルに保存する。"""

    def __init__(
        self,
        table: str,
        registry: str,
        client: NotificationStoreClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.table = table
        self.registry = registry
        self._client = client
        self._clock = clock

    def _audience_pk(self, audience: str) -> str:
        return f"REGISTRY#{self.registry}#NOTIFICATION#{audience}"

    def _key(self, pk: str, sk: str) -> dict[str, dict[str, str]]:
        return {"pk": {"S": pk}, "sk": {"S": sk}}

    def _expires_at(self) -> int:
        return int(self._clock()) + NOTIFICATION_RETENTION_SECONDS

    def _put(
        self,
        item: Mapping[str, Any],
        condition: str = "attribute_not_exists(pk)",
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        put: dict[str, Any] = {
            "TableName": self.table,
            "Item": _serialize({**item, _TTL_ATTRIBUTE: self._expires_at()}),
            "ConditionExpression": condition,
        }
        if values:
            put["ExpressionAttributeValues"] = _serialize(values)
        return {"Put": put}

    def notification_put(self, notification: Notification) -> dict[str, Any]:
        """通知を作るトランザクションの項目。アプリ内の通知は、元の操作と同じトランザクションで書く。"""
        return self._put(
            {
                **notification.to_item(),
                "pk": self._audience_pk(notification.audience),
                "sk": notification.position,
            }
        )

    def publish(self, notification: Notification, dedup_key: str) -> bool:
        """重複除去の印と通知を同時に書く。同じ出来事・宛先・種別の通知がすでにあれば False。"""
        marker = self._put(
            {
                "pk": f"REGISTRY#{self.registry}#NOTIFIED#{dedup_key}",
                "sk": f"{notification.audience}#{notification.kind}",
                "notificationId": notification.notification_id,
            }
        )
        try:
            self._client.transact_write_items(
                TransactItems=[marker, self.notification_put(notification)]
            )
        except ClientError as error:
            if _condition_failed(error):
                return False
            raise
        return True

    def _event_key(self, event_id: str) -> dict[str, dict[str, str]]:
        return self._key(f"REGISTRY#{self.registry}#EVENT#{event_id}", "EVENT")

    def event_processed(self, event_id: str) -> bool:
        """同じ event ID のイベントを処理し終えているか（EventBridge の重複配信）。"""
        item = self._client.get_item(
            TableName=self.table, Key=self._event_key(event_id), ConsistentRead=True
        ).get("Item")
        return item is not None

    def mark_event_processed(self, event_id: str) -> None:
        """イベントの処理を終えた印。通知をすべて書いた後に書き、途中で失敗したら再処理させる。"""
        key = self._event_key(event_id)
        put = self._put({"pk": key["pk"]["S"], "sk": key["sk"]["S"]})
        try:
            self._client.transact_write_items(TransactItems=[put])
        except ClientError as error:
            if not _condition_failed(error):
                raise

    def latest(self, audience: str, limit: int) -> list[Notification]:
        """宛先グループの新しい通知から limit 件。期限切れ（TTL の削除待ち）は除く。"""
        response = self._client.query(
            TableName=self.table,
            KeyConditionExpression="pk = :pk",
            FilterExpression=f"{_TTL_ATTRIBUTE} > :now",
            ExpressionAttributeValues=_serialize(
                {":pk": self._audience_pk(audience), ":now": int(self._clock())}
            ),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [Notification.parse(_deserialize(raw)) for raw in response.get("Items", [])]

    def count_after(self, audience: str, position: str | None) -> int:
        """宛先グループの通知のうち、既読位置より後のもの（未読）の件数。期限切れは数えない。"""
        condition = "pk = :pk"
        values: dict[str, Any] = {":pk": self._audience_pk(audience), ":now": int(self._clock())}
        if position is not None:
            condition += " AND sk > :position"
            values[":position"] = position
        params: dict[str, Any] = {
            "TableName": self.table,
            "KeyConditionExpression": condition,
            "FilterExpression": f"{_TTL_ATTRIBUTE} > :now",
            "ExpressionAttributeValues": _serialize(values),
            "Select": "COUNT",
        }
        count = 0
        # Query は1回の応答が 1MB までのため、LastEvaluatedKey がなくなるまで続きを数える
        while True:
            response = self._client.query(**params)
            count += int(response.get("Count", 0))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return count
            params["ExclusiveStartKey"] = last_key

    def _read_key(self, principal: PrincipalRecord) -> dict[str, dict[str, str]]:
        return self._key(
            f"REGISTRY#{self.registry}#READ#{principal['issuer']}#{principal['subject']}", "READ"
        )

    def read_position(self, principal: PrincipalRecord) -> str | None:
        """主体の既読位置。保存していない（または期限切れ）なら None。型が違えば ValueError。"""
        item = self._client.get_item(
            TableName=self.table, Key=self._read_key(principal), ConsistentRead=True
        ).get("Item")
        if item is None:
            return None
        stored = _deserialize(item)
        expires_at = stored.get(_TTL_ATTRIBUTE)
        if expires_at is not None and int(expires_at) <= int(self._clock()):
            return None
        position = stored.get("readUpTo")
        if not isinstance(position, str):
            raise ValueError("保存された既読位置の形式が正しくありません。")
        return position

    def save_read_position(self, principal: PrincipalRecord, position: str) -> None:
        """既読位置を進める。保存済みの位置より前には戻さない（複数のタブからの更新に備える）。"""
        key = self._read_key(principal)
        put = self._put(
            {"pk": key["pk"]["S"], "sk": key["sk"]["S"], "readUpTo": position},
            # TTL の削除待ちの期限切れの位置は、保存していないものとして上書きする
            f"attribute_not_exists(pk) OR readUpTo < :position OR {_TTL_ATTRIBUTE} <= :now",
            {":position": position, ":now": int(self._clock())},
        )
        try:
            self._client.transact_write_items(TransactItems=[put])
        except ClientError as error:
            if not _condition_failed(error):
                raise
