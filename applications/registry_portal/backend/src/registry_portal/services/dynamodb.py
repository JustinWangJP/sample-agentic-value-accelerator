"""補助テーブルのストアが共有する、DynamoDB の項目の変換とトランザクションの項目の組み立て。

補助ストア（supplement_store.py）・通知のストア（notification_store.py）・関連参照の逆引き
（reference_index.py）は、同じ表に同じ形で書くため、変換と条件付き Put の形をここだけで決める。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NewType, NotRequired, TypedDict

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

# 補助テーブルの TTL 属性名。テーブルの TTL 設定でこの属性を指定する（README「custom Agent の下書き」）
TTL_ATTRIBUTE = "expiresAt"

# TransactWriteItems に渡す項目1件（Put・Delete など）。別のストアが組み立てた項目を、
# 呼び出し側の書き込みと同じトランザクションに加えるときに受け渡す
TransactWriteItem = NewType("TransactWriteItem", dict[str, Any])


class QueryParams(TypedDict):
    """DynamoDB Query に渡す引数。2ページ目以降は ExclusiveStartKey を付ける。"""

    TableName: str
    KeyConditionExpression: str
    ExpressionAttributeValues: dict[str, Any]
    ExclusiveStartKey: NotRequired[dict[str, Any]]


def serialize(item: Mapping[str, Any]) -> dict[str, Any]:
    """Python の値を DynamoDB の型付きの項目にする。"""
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in item.items()}


def deserialize(item: Mapping[str, Any]) -> dict[str, Any]:
    """DynamoDB の型付きの項目を Python の値に戻す。"""
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in item.items()}


def put_item(
    table: str,
    item: Mapping[str, Any],
    condition: str | None = "attribute_not_exists(pk)",
    values: Mapping[str, Any] | None = None,
    names: Mapping[str, str] | None = None,
) -> TransactWriteItem:
    """条件付きの Put の項目。names は条件式で予約語（state など）を置き換える属性名。

    condition に None を渡すと、条件なしで上書きする（逆引きの索引など、同じ内容を書き直すもの）。
    """
    put: dict[str, Any] = {"TableName": table, "Item": serialize(item)}
    if condition is not None:
        put["ConditionExpression"] = condition
    if values:
        put["ExpressionAttributeValues"] = serialize(values)
    if names:
        put["ExpressionAttributeNames"] = dict(names)
    return TransactWriteItem({"Put": put})


def failed_conditions(error: ClientError) -> tuple[int, ...]:
    """トランザクションが条件式で取り消されたときの、条件を満たさなかった項目の位置。

    条件式以外の理由の失敗（スロットリングなど）では空のタプルを返す。
    """
    if error.response["Error"]["Code"] != "TransactionCanceledException":
        return ()
    reasons = error.response.get("CancellationReasons", [])
    return tuple(
        index
        for index, reason in enumerate(reasons)
        if reason.get("Code") == "ConditionalCheckFailed"
    )
