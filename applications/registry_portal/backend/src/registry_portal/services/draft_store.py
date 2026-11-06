from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Protocol, cast

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from registry_portal.domain.drafts import (
    SETTLED_OPERATION_RETENTION_SECONDS,
    DraftOperation,
    DraftResult,
    Management,
    OperationState,
)

# 補助テーブルの TTL 属性名。テーブルの TTL 設定でこの属性を指定する（README「custom Agent の下書き」）
TTL_ATTRIBUTE = "expiresAt"


class DraftStoreClient(Protocol):
    """下書きの補助ストアが使う DynamoDB クライアントの操作。"""

    def get_item(
        self, *, TableName: str, Key: dict[str, dict[str, str]], ConsistentRead: bool
    ) -> Mapping[str, Any]: ...

    def transact_write_items(self, *, TransactItems: list[dict[str, Any]]) -> Mapping[str, Any]: ...


class StoreConflict(Exception):
    """別の操作が排他権を持つか、同じキーの操作が既にある。"""


class DraftStore:
    """補助 DynamoDB テーブルの下書き用の項目（管理情報・ロック・操作・追記記録）を扱う。

    キーの組み立てはこのクラスに閉じ込め、呼び出し側には操作 ID や Registry レコード ID だけを渡させる。
    """

    def __init__(self, table: str, registry: str, client: DraftStoreClient) -> None:
        self.table = table
        self.registry = registry
        self._client = client

    def record_pk(self, record_id: str) -> str:
        return f"REGISTRY#{self.registry}#RECORD#{record_id}"

    def name_lock_pk(self, name: str) -> str:
        """作成中でレコード ID がまだない Registry レコードを、名前で排他するためのキー。"""
        return f"REGISTRY#{self.registry}#NAME#{name}"

    @staticmethod
    def _operation_pk(op_id: str) -> str:
        return f"OP#{op_id}"

    def _key(self, pk: str, sk: str) -> dict[str, Any]:
        return {"pk": {"S": pk}, "sk": {"S": sk}}

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self._client.get_item(
            TableName=self.table, Key=self._key(pk, sk), ConsistentRead=True
        ).get("Item")
        if item is None:
            return None
        return {key: TypeDeserializer().deserialize(value) for key, value in item.items()}

    def get_operation(self, op_id: str) -> DraftOperation | None:
        return cast("DraftOperation | None", self.get(self._operation_pk(op_id), "CONTROL"))

    def get_management(self, record_id: str) -> Management | None:
        """管理情報を読む。保存内容の型が契約と異なれば ValueError。"""
        item = self.get(self.record_pk(record_id), "MANAGEMENT")
        return None if item is None else Management.parse(item)

    def get_lock(self, record_id: str) -> dict[str, Any] | None:
        return self.get(self.record_pk(record_id), "LOCK")

    def _put(
        self,
        item: Mapping[str, Any],
        condition: str = "attribute_not_exists(pk)",
        values: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        put: dict[str, Any] = {
            "TableName": self.table,
            "Item": {key: TypeSerializer().serialize(value) for key, value in item.items()},
            "ConditionExpression": condition,
        }
        if values:
            put["ExpressionAttributeValues"] = {
                name: {"S": value} for name, value in values.items()
            }
        return {"Put": put}

    def _release_lock(self, pk: str, op_id: str) -> dict[str, Any]:
        return {
            "Delete": {
                "TableName": self.table,
                "Key": self._key(pk, "LOCK"),
                "ConditionExpression": "operationId = :op",
                "ExpressionAttributeValues": {":op": {"S": op_id}},
            }
        }

    def _release_locks(
        self, operation: DraftOperation, record_id: str | None
    ) -> list[dict[str, Any]]:
        """操作が持つロックをすべて解除する。作成では名前のロックとレコードのロックの2つを持つ。"""
        op_id = operation["operationId"]
        items = [self._release_lock(operation["lockPk"], op_id)]
        if operation["kind"] == "CREATE" and record_id:
            items.append(self._release_lock(self.record_pk(record_id), op_id))
        return items

    def _transact(self, items: list[dict[str, Any]]) -> None:
        try:
            self._client.transact_write_items(TransactItems=items)
        except ClientError as error:
            if error.response["Error"]["Code"] == "TransactionCanceledException" and any(
                reason.get("Code") == "ConditionalCheckFailed"
                for reason in error.response.get("CancellationReasons", [])
            ):
                raise StoreConflict() from None
            raise

    def begin(self, operation: DraftOperation) -> None:
        op_id, lock = operation["operationId"], operation["lockPk"]
        self._transact(
            [
                self._put({"pk": lock, "sk": "LOCK", "operationId": op_id}),
                self._put({**operation, "pk": self._operation_pk(op_id), "sk": "CONTROL"}),
                self._put({**operation, "pk": lock, "sk": f"AUDIT#{op_id}#INTENT"}),
            ]
        )

    def save_pending(self, operation: DraftOperation) -> None:
        op_id = operation["operationId"]
        control = self._put(
            {**operation, "pk": self._operation_pk(op_id), "sk": "CONTROL", "state": "VERIFYING"},
            "operationId = :op AND #state IN (:running, :verifying)",
            {":op": op_id, ":running": "IN_PROGRESS", ":verifying": "VERIFYING"},
        )
        control["Put"]["ExpressionAttributeNames"] = {"#state": "state"}
        record_pk = self.record_pk(operation["recordId"])
        team = {":team": operation["ownerTeam"]}
        if operation["kind"] == "CREATE":
            owner = self._put(
                {
                    "pk": record_pk,
                    "sk": "MANAGEMENT",
                    "managed": True,
                    "ownerTeam": operation["ownerTeam"],
                },
                "attribute_not_exists(pk) OR ownerTeam = :team",
                team,
            )
            lock = self._put(
                {"pk": record_pk, "sk": "LOCK", "operationId": op_id},
                "attribute_not_exists(pk) OR operationId = :op",
                {":op": op_id},
            )
            items = [control, owner, lock]
        else:
            owner = self._put(
                {**operation["management"], "pk": record_pk, "sk": "MANAGEMENT"},
                "ownerTeam = :team",
                team,
            )
            items = [control, owner]
        self._transact(items)

    def _close(
        self,
        operation: DraftOperation,
        state: OperationState,
        outcome: Mapping[str, Any],
        record_id: str | None,
    ) -> list[dict[str, Any]]:
        """操作を確定し、結果を追記記録に残し、ロックを解除する項目を返す。

        確定した操作（実行制御）には TTL を付け、保持期間の後に DynamoDB が削除する。
        追記記録（操作記録）は無期限に保持する。
        """
        op_id = operation["operationId"]
        if "awsRequestId" in operation:
            outcome = {**outcome, "awsRequestId": operation["awsRequestId"]}
        return [
            self._put(
                {
                    **operation,
                    **outcome,
                    "pk": self._operation_pk(op_id),
                    "sk": "CONTROL",
                    "state": state,
                    TTL_ATTRIBUTE: int(time.time()) + SETTLED_OPERATION_RETENTION_SECONDS,
                },
                "operationId = :op",
                {":op": op_id},
            ),
            self._put({**outcome, "pk": operation["lockPk"], "sk": f"AUDIT#{op_id}#RESULT"}),
            *self._release_locks(operation, record_id),
        ]

    def abort(self, operation: DraftOperation, code: str) -> None:
        self._transact(
            self._close(operation, "FAILED", {"errorCode": code}, operation.get("recordId"))
        )

    def finish(
        self,
        operation: DraftOperation,
        result: DraftResult,
        management: Management,
    ) -> None:
        owner = self._put(
            {**management.item, "pk": self.record_pk(result["recordId"]), "sk": "MANAGEMENT"},
            "attribute_not_exists(pk) OR ownerTeam = :team",
            {":team": operation["ownerTeam"]},
        )
        self._transact(
            [owner, *self._close(operation, "SUCCEEDED", {"result": result}, result["recordId"])]
        )
