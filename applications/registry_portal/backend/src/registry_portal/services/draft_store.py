from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from registry_portal.domain.drafts import DraftOperation, OperationState


class StoreConflict(Exception):
    """別の操作が排他権を持つか、同じキーの操作が既にある。"""


class DraftStore:
    """補助 DynamoDB テーブルの下書き用の項目（管理情報・ロック・操作・追記記録）を扱う。

    キーの組み立てはこのクラスに閉じ込め、呼び出し側には操作 ID や Registry レコード ID だけを渡させる。
    """

    def __init__(self, table: str, registry: str, region: str, client: Any = None) -> None:
        self.table = table
        self.registry = registry
        self.client = client
        self.region = region

    def _client(self) -> Any:
        if self.client is None:
            self.client = boto3.client("dynamodb", region_name=self.region)
        return self.client

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
        item = (
            self._client()
            .get_item(TableName=self.table, Key=self._key(pk, sk), ConsistentRead=True)
            .get("Item")
        )
        if item is None:
            return None
        return {key: TypeDeserializer().deserialize(value) for key, value in item.items()}

    def get_operation(self, op_id: str) -> DraftOperation | None:
        return cast("DraftOperation | None", self.get(self._operation_pk(op_id), "CONTROL"))

    def get_management(self, record_id: str) -> dict[str, Any] | None:
        return self.get(self.record_pk(record_id), "MANAGEMENT")

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
            self._client().transact_write_items(TransactItems=items)
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
        """操作を確定し、結果を追記記録に残し、ロックを解除する項目を返す。"""
        op_id = operation["operationId"]
        return [
            self._put(
                {
                    **operation,
                    **outcome,
                    "pk": self._operation_pk(op_id),
                    "sk": "CONTROL",
                    "state": state,
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
        result: dict[str, Any],
        management: dict[str, Any],
    ) -> None:
        owner = self._put(
            {**management, "pk": self.record_pk(result["recordId"]), "sk": "MANAGEMENT"},
            "attribute_not_exists(pk) OR ownerTeam = :team",
            {":team": operation["ownerTeam"]},
        )
        self._transact(
            [owner, *self._close(operation, "SUCCEEDED", {"result": result}, result["recordId"])]
        )
