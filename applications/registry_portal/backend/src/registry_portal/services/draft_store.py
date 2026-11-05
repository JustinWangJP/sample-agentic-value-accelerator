"""条件付きロックと下書き操作の意図・結果を1テーブルで保存する。"""

from __future__ import annotations

from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError


class StoreConflict(Exception):
    """別の操作が排他権を持つか、同じキーの操作が既にある。"""


class DraftStore:
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

    def _put(
        self, item: dict[str, Any], condition: str = "attribute_not_exists(pk)"
    ) -> dict[str, Any]:
        return {
            "Put": {
                "TableName": self.table,
                "Item": {key: TypeSerializer().serialize(value) for key, value in item.items()},
                "ConditionExpression": condition,
            }
        }

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

    def begin(self, operation: dict[str, Any]) -> None:
        op_id, lock = operation["operationId"], operation["lockPk"]
        self._transact(
            [
                self._put({"pk": lock, "sk": "LOCK", "operationId": op_id}),
                self._put({**operation, "pk": f"OP#{op_id}", "sk": "CONTROL"}),
                self._put({**operation, "pk": lock, "sk": f"AUDIT#{op_id}#INTENT"}),
            ]
        )

    def save_pending(self, operation: dict[str, Any]) -> None:
        op_id = operation["operationId"]
        control = self._put(
            {**operation, "pk": f"OP#{op_id}", "sk": "CONTROL", "state": "VERIFYING"},
            "operationId = :op AND #state IN (:running, :verifying)",
        )
        control["Put"]["ExpressionAttributeNames"] = {"#state": "state"}
        control["Put"]["ExpressionAttributeValues"] = {
            ":op": {"S": op_id},
            ":running": {"S": "IN_PROGRESS"},
            ":verifying": {"S": "VERIFYING"},
        }
        items = [control]
        if operation["kind"] == "CREATE":
            record_pk = self.record_pk(operation["recordId"])
            owner = self._put(
                {
                    "pk": record_pk,
                    "sk": "MANAGEMENT",
                    "managed": True,
                    "ownerTeam": operation["ownerTeam"],
                },
                "attribute_not_exists(pk) OR ownerTeam = :team",
            )
            owner["Put"]["ExpressionAttributeValues"] = {":team": {"S": operation["ownerTeam"]}}
            lock = self._put(
                {"pk": record_pk, "sk": "LOCK", "operationId": op_id},
                "attribute_not_exists(pk) OR operationId = :op",
            )
            lock["Put"]["ExpressionAttributeValues"] = {":op": {"S": op_id}}
            items.extend([owner, lock])
        else:
            owner = self._put(
                {
                    **operation["management"],
                    "pk": self.record_pk(operation["recordId"]),
                    "sk": "MANAGEMENT",
                },
                "ownerTeam = :team",
            )
            owner["Put"]["ExpressionAttributeValues"] = {":team": {"S": operation["ownerTeam"]}}
            items.append(owner)
        self._transact(items)

    def abort(self, operation: dict[str, Any], code: str) -> None:
        op_id = operation["operationId"]
        control = self._put(
            {
                **operation,
                "pk": f"OP#{op_id}",
                "sk": "CONTROL",
                "state": "FAILED",
                "errorCode": code,
            },
            "operationId = :op",
        )
        control["Put"]["ExpressionAttributeValues"] = {":op": {"S": op_id}}
        self._transact(
            [
                control,
                self._put(
                    {
                        "pk": operation["lockPk"],
                        "sk": f"AUDIT#{op_id}#RESULT",
                        "errorCode": code,
                    }
                ),
                {
                    "Delete": {
                        "TableName": self.table,
                        "Key": self._key(operation["lockPk"], "LOCK"),
                        "ConditionExpression": "operationId = :op",
                        "ExpressionAttributeValues": {":op": {"S": op_id}},
                    }
                },
            ]
        )

    def finish(
        self,
        operation: dict[str, Any],
        result: dict[str, Any],
        management: dict[str, Any],
    ) -> None:
        self._transact(self._finish_items(operation, result, management))

    def _finish_items(
        self,
        operation: dict[str, Any],
        result: dict[str, Any],
        management: dict[str, Any],
    ) -> list[dict[str, Any]]:
        op_id = operation["operationId"]
        owner = self._put(
            {
                **management,
                "pk": self.record_pk(result["recordId"]),
                "sk": "MANAGEMENT",
            },
            "attribute_not_exists(pk) OR ownerTeam = :team",
        )
        owner["Put"]["ExpressionAttributeValues"] = {":team": {"S": operation["ownerTeam"]}}
        control = self._put(
            {
                **operation,
                "pk": f"OP#{op_id}",
                "sk": "CONTROL",
                "state": "SUCCEEDED",
                "result": result,
            },
            "operationId = :op",
        )
        control["Put"]["ExpressionAttributeValues"] = {":op": {"S": op_id}}
        items = [
            owner,
            control,
            self._put(
                {
                    "pk": operation["lockPk"],
                    "sk": f"AUDIT#{op_id}#RESULT",
                    "result": result,
                }
            ),
            {
                "Delete": {
                    "TableName": self.table,
                    "Key": self._key(operation["lockPk"], "LOCK"),
                    "ConditionExpression": "operationId = :op",
                    "ExpressionAttributeValues": {":op": {"S": op_id}},
                }
            },
        ]
        if operation["kind"] == "CREATE":
            items.append(
                {
                    "Delete": {
                        "TableName": self.table,
                        "Key": self._key(self.record_pk(result["recordId"]), "LOCK"),
                        "ConditionExpression": "operationId = :op",
                        "ExpressionAttributeValues": {":op": {"S": op_id}},
                    }
                }
            )
        return items
