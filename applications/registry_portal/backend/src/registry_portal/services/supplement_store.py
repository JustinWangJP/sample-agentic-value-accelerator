from __future__ import annotations

import time
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, NamedTuple, NotRequired, Protocol, TypedDict, Unpack, cast

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from registry_portal.domain.administration import AdministrationResult
from registry_portal.domain.catalog import published_snapshot_sk
from registry_portal.domain.deprecation import (
    DEPRECATION_OPEN,
    DEPRECATION_RESOLVED,
    DeprecationRequest,
)
from registry_portal.domain.drafts import (
    AUDIT_INTENT,
    AUDIT_RESULT,
    AUDIT_SK_PREFIX,
    CREATE_KIND,
    MANAGEMENT_FIELDS,
    SETTLED_OPERATION_RETENTION_SECONDS,
    UPDATE_KIND,
    DecisionOperation,
    DraftOperation,
    DraftResult,
    Management,
    OperationKind,
    OperationRecord,
    OperationState,
    PrincipalRecord,
    StoredOperation,
    AuditPhase,
    audit_sk,
    is_decision_operation,
    parse_audit_sk,
)

# 補助テーブルの TTL 属性名。テーブルの TTL 設定でこの属性を指定する（README「custom Agent の下書き」）
TTL_ATTRIBUTE = "expiresAt"
# 取り込み・所有変更の再送用の結果の sk。保存と読み取りで同じ値を使う
ADMIN_RECEIPT_SK = "ADMIN_RECEIPT"

# 作成の操作の INTENT をレコード単位に写すときに含めない項目。
# recordId と awsRequestId は開始（begin）より後に書き足すため、開始時の意図に揃えるよう除く。
# 定義の全文（draft）は正本（名前のロック側の INTENT）にだけ残し、無期限に残る写しには重ねて保存しない
_EXCLUDED_FROM_INTENT_COPY = frozenset({"recordId", "awsRequestId", "draft", "pk", "sk"})


def _deserialize(item: Mapping[str, Any]) -> dict[str, Any]:
    """DynamoDB の型付きの項目を Python の値に戻す。"""
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in item.items()}


class QueryParams(TypedDict):
    """DynamoDB Query に渡す引数。2ページ目以降は ExclusiveStartKey を付ける。"""

    TableName: str
    KeyConditionExpression: str
    ExpressionAttributeValues: dict[str, Any]
    ExclusiveStartKey: NotRequired[dict[str, Any]]


class SupplementStoreClient(Protocol):
    """下書きの補助ストアが使う DynamoDB クライアントの操作。"""

    def get_item(
        self, *, TableName: str, Key: dict[str, dict[str, str]], ConsistentRead: bool
    ) -> Mapping[str, Any]: ...

    def transact_write_items(self, *, TransactItems: list[dict[str, Any]]) -> Mapping[str, Any]: ...

    def query(self, **kwargs: Unpack[QueryParams]) -> Mapping[str, Any]: ...


class AppendedRecord(NamedTuple):
    """操作記録の追記記録1件（GLOSSARY「操作記録」）。ソートキーから取り出した操作 ID と段階を、保存した内容と組にする。"""

    operation_id: str
    phase: AuditPhase
    item: dict[str, Any]


class StoreConflict(Exception):
    """別の操作が排他権を持つか、同じキーの操作が既にある。

    failed は、条件を満たさなかったトランザクションの項目の位置。呼び出し側が理由を区別するのに使う。
    """

    def __init__(self, failed: tuple[int, ...] = ()) -> None:
        super().__init__()
        self.failed = failed


class RecordLocked(StoreConflict):
    """別の操作（公開停止・廃止の処理中など）がレコードのロックを持つため、書き込めなかった。"""


class InstantOperation(TypedDict):
    """補助テーブルへの書き込みだけで完結する操作（廃止依頼・見送り）の操作記録。

    実行制御（CONTROL）とロックを持たず、INTENT と RESULT を1つのトランザクションで追記する。
    履歴 API（history.py）は、ほかの操作と同じく INTENT と RESULT の組として読む。
    """

    operationId: str
    kind: OperationKind
    principal: PrincipalRecord
    startedAt: str
    ownerTeam: str
    recordId: str
    reason: str
    expectedContentHash: NotRequired[str]
    previousOwnerTeam: NotRequired[str]
    recordVersion: NotRequired[str]


class AdministrationReceipt(TypedDict):
    """取り込み・所有変更の再送用の結果。同じ Idempotency-Key と内容には同じ結果を返す。"""

    requestHash: str
    result: AdministrationResult


class AdministrationChange(NamedTuple):
    """取り込み・所有変更で1つの transaction に保存する内容。

    previous は読取時の管理情報（未作成なら None）で、保存時に全項目が変わっていないことを検査する。
    published は取り込み時の公開版スナップショット（公開版がなければ None）。
    """

    previous: Management | None
    management: Management
    published: Mapping[str, Any] | None
    audit: InstantOperation
    receipt: AdministrationReceipt


class SupplementStore:
    """補助 DynamoDB テーブルの項目を扱う。

    扱う項目は、管理情報・ロック・操作・追記記録・申請スナップショット・公開版スナップショット・廃止依頼。
    ポータルの書き込みはすべて同じロックで排他するため、
    廃止依頼もロックを確かめる同じトランザクションの中で扱い、別のストアに分けない。
    キーの組み立てはこのクラスに閉じ込め、呼び出し側には操作 ID や Registry レコード ID だけを渡させる。
    """

    def __init__(self, table: str, registry: str, client: SupplementStoreClient) -> None:
        self.table = table
        self.registry = registry
        self._client = client

    def record_pk(self, record_id: str) -> str:
        return f"REGISTRY#{self.registry}#RECORD#{record_id}"

    def name_lock_pk(self, name: str) -> str:
        """作成中でレコード ID がまだない Registry レコードを、名前で排他するためのキー。"""
        return f"REGISTRY#{self.registry}#NAME#{name}"

    def deprecation_pk(self) -> str:
        """廃止依頼（レコードをまたぐ一覧用）のパーティションキー。ソートキーはレコード ID。"""
        return f"REGISTRY#{self.registry}#DEPRECATION"

    @staticmethod
    def _operation_pk(op_id: str) -> str:
        return f"OP#{op_id}"

    @staticmethod
    def _submission_sk(submission_id: str) -> str:
        """申請スナップショットのソートキー。申請 ID は申請操作の操作 ID と同じ。"""
        return f"SUBMISSION#{submission_id}"

    @classmethod
    def _invalidated_sk(cls, submission_id: str) -> str:
        """承認待ちの間の編集で申請を無効にした記録のソートキー。スナップショット自体は変更しない。"""
        return f"{cls._submission_sk(submission_id)}#INVALIDATED"

    def _key(self, pk: str, sk: str) -> dict[str, Any]:
        return {"pk": {"S": pk}, "sk": {"S": sk}}

    def get_deprecation_request(self, record_id: str) -> DeprecationRequest | None:
        """廃止依頼1件を読む。保存内容の型が契約と異なれば ValueError。"""
        item = self.get(self.deprecation_pk(), record_id)
        return None if item is None else DeprecationRequest.parse(item)

    def list_deprecation_requests(self) -> list[DeprecationRequest]:
        """廃止依頼をすべて読む。審査待ちの一覧が未解決の依頼だけを表示する。

        Query は1回の応答が 1MB までのため、LastEvaluatedKey がなくなるまで続きを読む。
        保存内容の型が契約と異なれば ValueError。
        """
        params: QueryParams = {
            "TableName": self.table,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": {"S": self.deprecation_pk()}},
        }
        requests: list[DeprecationRequest] = []
        while True:
            response = self._client.query(**params)
            for raw in response.get("Items", []):
                requests.append(DeprecationRequest.parse(_deserialize(raw)))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return requests
            params["ExclusiveStartKey"] = last_key

    def save_deprecation_request(
        self,
        request: DeprecationRequest,
        audit: InstantOperation,
        notification: Mapping[str, Any],
    ) -> None:
        """廃止依頼を保存し、操作記録を追記する。notification はアプリ内の通知の項目（同じトランザクションで書く）。

        別の操作がロックを持てば RecordLocked、未解決の依頼がすでにあれば StoreConflict。
        解決済みの依頼は新しい依頼で上書きする（レコードごとに依頼は1件だけ残る）。
        """
        self._transact_unlocked(
            self._put(
                {**request.to_item(), "pk": self.deprecation_pk(), "sk": request.record_id},
                "attribute_not_exists(pk) OR #state = :resolved",
                {":resolved": DEPRECATION_RESOLVED},
                names={"#state": "state"},
            ),
            audit,
            notification,
        )

    def decline_deprecation_request(
        self,
        declined: DeprecationRequest,
        audit: InstantOperation,
        notification: Mapping[str, Any],
    ) -> None:
        """未解決の廃止依頼を見送りとして解決し、操作記録を追記する。

        別の操作がロックを持てば RecordLocked（公開停止・廃止の処理中など）、
        依頼が未解決でないか別の依頼に置き換わっていれば StoreConflict。
        """
        self._transact_unlocked(self._resolve_deprecation_request(declined), audit, notification)

    def _transact_unlocked(
        self,
        write: dict[str, Any],
        audit: InstantOperation,
        notification: Mapping[str, Any],
    ) -> None:
        """レコードのロックがないことを確かめながら、1件の書き込みと操作記録の追記を行う。

        notification（アプリ内の通知の項目）も同じトランザクションで書く。書き込みが
        取り消されれば通知も残らず、書き込みが成功すれば通知も必ず残る。

        トランザクションの項目の位置はこのメソッドの中だけで扱い、呼び出し側には例外の種類で伝える。
        """
        lock_check = self._no_lock(audit["recordId"])
        items = [write, lock_check, *self._instant_audit(audit), dict(notification)]
        try:
            self._transact(items)
        except StoreConflict as conflict:
            if items.index(lock_check) in conflict.failed:
                raise RecordLocked(conflict.failed) from None
            raise

    def _resolve_deprecation_request(self, resolved: DeprecationRequest) -> dict[str, Any]:
        """未解決の同じ依頼（依頼 ID が一致）を解決済みにする項目。条件を満たさなければ失敗する。"""
        return self._put(
            {**resolved.to_item(), "pk": self.deprecation_pk(), "sk": resolved.record_id},
            "#state = :open AND requestId = :request",
            {":open": DEPRECATION_OPEN, ":request": resolved.request_id},
            names={"#state": "state"},
        )

    def _no_lock(self, record_id: str) -> dict[str, Any]:
        """レコードのロックがないことを確かめる項目。すべての書き込みは共通のロックの外で行わない。"""
        return {
            "ConditionCheck": {
                "TableName": self.table,
                "Key": self._key(self.record_pk(record_id), "LOCK"),
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }

    def _instant_audit(self, audit: InstantOperation) -> list[dict[str, Any]]:
        """INTENT と RESULT を同時に追記する項目。結果は書き込みの成功そのものなので、RESULT は空の結果を持つ。"""
        pk = self.record_pk(audit["recordId"])
        op_id = audit["operationId"]
        return [
            self._put({**audit, "pk": pk, "sk": audit_sk(op_id, AUDIT_INTENT)}),
            self._put({"operationId": op_id, "pk": pk, "sk": audit_sk(op_id, AUDIT_RESULT)}),
        ]

    def administration_receipt(self, op_id: str) -> AdministrationReceipt | None:
        """瞬時の Admin 操作の確定結果。AWS 操作の CONTROL と回復経路から分ける。"""
        return cast(
            "AdministrationReceipt | None",
            self.get(self._operation_pk(op_id), ADMIN_RECEIPT_SK),
        )

    def save_administration(self, change: AdministrationChange) -> None:
        """管理情報・公開版・監査・再送の結果を、共通ロックと CAS で原子的に保存する。"""
        previous, management, published, audit, receipt = change
        record_pk = self.record_pk(audit["recordId"])
        condition = "attribute_not_exists(pk)"
        values: dict[str, Any] = {}
        names: dict[str, str] = {}
        if previous is not None:
            fields = (set(previous.item) - {"pk", "sk"}) | set(MANAGEMENT_FIELDS)
            clauses = []
            for index, field in enumerate(sorted(fields)):
                name, value = f"#f{index}", f":v{index}"
                names[name] = field
                if field in previous.item:
                    clauses.append(f"{name} = {value}")
                    values[value] = previous.item[field]
                else:
                    clauses.append(f"attribute_not_exists({name})")
            condition = " AND ".join(clauses)
        items = [
            self._put(
                {**management.item, "pk": record_pk, "sk": "MANAGEMENT"},
                condition,
                values or None,
                names=names or None,
            )
        ]
        if published is not None:
            version = management.adopted_published_version
            if version is None:
                raise ValueError("公開版スナップショットには取り込み時の公開版が必要です。")
            items.append(
                self._put({**published, "pk": record_pk, "sk": published_snapshot_sk(version)})
            )
        lock_check = self._no_lock(audit["recordId"])
        items += [
            lock_check,
            *self._instant_audit(audit),
            self._put(
                {
                    **receipt,
                    "pk": self._operation_pk(audit["operationId"]),
                    "sk": ADMIN_RECEIPT_SK,
                    TTL_ATTRIBUTE: int(time.time()) + SETTLED_OPERATION_RETENTION_SECONDS,
                }
            ),
        ]
        try:
            self._transact(items)
        except StoreConflict as conflict:
            if items.index(lock_check) in conflict.failed:
                raise RecordLocked(conflict.failed) from None
            raise

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self._client.get_item(
            TableName=self.table, Key=self._key(pk, sk), ConsistentRead=True
        ).get("Item")
        return None if item is None else _deserialize(item)

    def get_operation(self, op_id: str) -> StoredOperation | None:
        """操作を種別を問わず読む。種別ごとの項目は is_decision_operation で見分ける。"""
        return cast("StoredOperation | None", self.get(self._operation_pk(op_id), "CONTROL"))

    def get_draft_operation(self, op_id: str) -> DraftOperation | None:
        """下書きの操作を読む。決裁の操作は下書きの操作として扱わず None にする。"""
        operation = self.get_operation(op_id)
        if operation is None or is_decision_operation(operation):
            return None
        return operation

    def get_decision_operation(self, op_id: str) -> DecisionOperation | None:
        """決裁の操作を読む。下書きの操作は決裁の操作として扱わず None にする。"""
        operation = self.get_operation(op_id)
        if operation is None or not is_decision_operation(operation):
            return None
        return operation

    def get_published(self, record_id: str, record_version: str) -> dict[str, Any] | None:
        """公開版スナップショット（ADR-0007）を読む。"""
        return self.get(self.record_pk(record_id), published_snapshot_sk(record_version))

    def get_management(self, record_id: str) -> Management | None:
        """管理情報を読む。保存内容の型が契約と異なれば ValueError。"""
        item = self.get(self.record_pk(record_id), "MANAGEMENT")
        return None if item is None else Management.parse(item)

    def get_lock(self, record_id: str) -> dict[str, Any] | None:
        return self.get(self.record_pk(record_id), "LOCK")

    def list_appended_records(self, record_id: str) -> list[AppendedRecord]:
        """レコードに追記した操作記録（AUDIT 項目）をすべて読む。順序は並び替えず呼び出し側に任せる。

        Query は1回の応答が 1MB までのため、LastEvaluatedKey がなくなるまで続きを読む
        （README「操作と審査の履歴」）。ソートキーの形式が契約と異なれば ValueError。
        """
        params: QueryParams = {
            "TableName": self.table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
            "ExpressionAttributeValues": {
                ":pk": {"S": self.record_pk(record_id)},
                ":sk": {"S": AUDIT_SK_PREFIX},
            },
        }
        records: list[AppendedRecord] = []
        while True:
            response = self._client.query(**params)
            for raw in response.get("Items", []):
                item = _deserialize(raw)
                records.append(AppendedRecord(*parse_audit_sk(item.get("sk")), item))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return records
            params["ExclusiveStartKey"] = last_key

    def get_submission(self, record_id: str, submission_id: str) -> dict[str, Any] | None:
        """申請スナップショットと、その申請が無効にされたか（`invalidated`）を返す。

        公開承認（#10）は、`invalidated` が真の申請を決裁してはならない。
        無効化記録はポータルでの編集だけが書くため、ポータル外の編集では `invalidated` は偽のままになる。
        #10 は決裁の前に、現物の内容ハッシュをスナップショットの `contentHash` と照合し直すこと。
        """
        pk = self.record_pk(record_id)
        snapshot = self.get(pk, self._submission_sk(submission_id))
        if snapshot is None:
            return None
        invalidated = self.get(pk, self._invalidated_sk(submission_id)) is not None
        return {**snapshot, "invalidated": invalidated}

    def _put(
        self,
        item: Mapping[str, Any],
        condition: str | None = "attribute_not_exists(pk)",
        values: Mapping[str, Any] | None = None,
        *,
        names: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """条件付きの Put の項目。names は条件式で予約語（state など）を置き換える属性名。

        condition に None を渡すと、条件なしで上書きする（逆引きの索引など、同じ内容を書き直すもの）。
        """
        put: dict[str, Any] = {
            "TableName": self.table,
            "Item": {key: TypeSerializer().serialize(value) for key, value in item.items()},
        }
        if condition is not None:
            put["ConditionExpression"] = condition
        if values:
            put["ExpressionAttributeValues"] = {
                name: TypeSerializer().serialize(value) for name, value in values.items()
            }
        if names:
            put["ExpressionAttributeNames"] = dict(names)
        return {"Put": put}

    def _put_control(
        self,
        operation: OperationRecord,
        state: OperationState,
        allowed_states: Mapping[str, OperationState],
    ) -> dict[str, Any]:
        """実行制御の項目を、操作 ID と現在の状態が想定どおりのときだけ書き換える。

        allowed_states は、条件式の値の名前（`:running` など）から許可する現在の状態への対応。
        """
        op_id = operation["operationId"]
        names = ", ".join(allowed_states)
        # state は DynamoDB の予約語のため、属性名を置き換える
        return self._put(
            {**operation, "pk": self._operation_pk(op_id), "sk": "CONTROL", "state": state},
            f"operationId = :op AND #state IN ({names})",
            {":op": op_id, **allowed_states},
            names={"#state": "state"},
        )

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
        self, operation: OperationRecord, record_id: str | None
    ) -> list[dict[str, Any]]:
        """操作が持つロックをすべて解除する。作成では名前のロックとレコードのロックの2つを持つ。"""
        op_id = operation["operationId"]
        items = [self._release_lock(operation["lockPk"], op_id)]
        if operation["kind"] == CREATE_KIND and record_id:
            items.append(self._release_lock(self.record_pk(record_id), op_id))
        return items

    def _transact(self, items: list[dict[str, Any]]) -> None:
        try:
            self._client.transact_write_items(TransactItems=items)
        except ClientError as error:
            reasons = error.response.get("CancellationReasons", [])
            failed = tuple(
                index
                for index, reason in enumerate(reasons)
                if reason.get("Code") == "ConditionalCheckFailed"
            )
            if error.response["Error"]["Code"] == "TransactionCanceledException" and failed:
                raise StoreConflict(failed) from None
            raise

    def begin(self, operation: OperationRecord) -> None:
        op_id, lock = operation["operationId"], operation["lockPk"]
        self._transact(
            [
                self._put({"pk": lock, "sk": "LOCK", "operationId": op_id}),
                self._put({**operation, "pk": self._operation_pk(op_id), "sk": "CONTROL"}),
                self._put({**operation, "pk": lock, "sk": audit_sk(op_id, AUDIT_INTENT)}),
            ]
        )

    def save_pending(self, operation: OperationRecord) -> None:
        op_id = operation["operationId"]
        control = self._put_control(
            operation, "VERIFYING", {":running": "IN_PROGRESS", ":verifying": "VERIFYING"}
        )
        record_pk = self.record_pk(operation["recordId"])
        team = {":team": operation["ownerTeam"]}
        if operation["kind"] == CREATE_KIND:
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
            # 作成はロックをレコード名で取るため、レコード ID が分かった時点で開始時の意図をレコード単位にも写す。
            # 結果が確定する前（結果確認中）から、レコードの履歴に操作が現れるようにする
            intent = {
                key: value
                for key, value in operation.items()
                if key not in _EXCLUDED_FROM_INTENT_COPY
            }
            intent_copy = self._put(
                {**intent, "pk": record_pk, "sk": audit_sk(op_id, AUDIT_INTENT)}
            )
            items = [control, owner, lock, intent_copy]
        else:
            owner = self._put(
                {**operation["management"], "pk": record_pk, "sk": "MANAGEMENT"},
                "ownerTeam = :team",
                team,
            )
            items = [control, owner]
        self._transact(items)

    def save_submission(self, operation: DraftOperation, snapshot: Mapping[str, Any]) -> None:
        control = self._put_control(operation, "VERIFYING", {":running": "IN_PROGRESS"})
        self._transact(
            [
                control,
                self._put(
                    {
                        **snapshot,
                        "pk": operation["lockPk"],
                        "sk": self._submission_sk(operation["operationId"]),
                    }
                ),
            ]
        )

    def _close(
        self,
        operation: OperationRecord,
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
        # Admin の回復操作で確定した操作は、回復した主体と理由を操作記録に残す。
        # 成功した操作は、操作照会が保存した結果をそのまま返すため、結果にも写す
        if "recovery" in operation:
            recovery = operation["recovery"]
            recorded: dict[str, Any] = {"recovery": recovery}
            if "result" in outcome:
                recorded["result"] = {**outcome["result"], "recovery": recovery}
            outcome = {**outcome, **recorded}
        # 操作記録には確定した時刻も残す。通知のイベント処理が、Registry の状態変化が
        # この操作の間に起きたか（ポータル内の変更か）を照合するのに使う
        audit_outcome = {**outcome, "settledAt": datetime.now(timezone.utc).isoformat()}
        audit_items = [
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
            self._put(
                {**audit_outcome, "pk": operation["lockPk"], "sk": audit_sk(op_id, AUDIT_RESULT)}
            ),
        ]
        # 作成の操作は、保存中（save_pending）にレコード単位へ写した INTENT と組になるよう、RESULT も写す。
        # 条件式（attribute_not_exists）付きのため、既存の記録を上書きしない
        target = record_id if record_id is not None else operation.get("recordId")
        if target is not None and self.record_pk(target) != operation["lockPk"]:
            audit_items.append(
                self._put(
                    {
                        **audit_outcome,
                        "pk": self.record_pk(target),
                        "sk": audit_sk(op_id, AUDIT_RESULT),
                    }
                )
            )
        return [*audit_items, *self._release_locks(operation, record_id)]

    def abort(self, operation: OperationRecord, code: str) -> None:
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
        items = [
            owner,
            *self._close(operation, "SUCCEEDED", {"result": result}, result["recordId"]),
        ]
        previous = operation.get("management", {}).get("activeSubmissionId")
        if operation["kind"] == UPDATE_KIND and previous:
            items.append(
                self._put(
                    {
                        "pk": operation["lockPk"],
                        "sk": self._invalidated_sk(previous),
                        "operationId": operation["operationId"],
                        "invalidatedAt": operation["startedAt"],
                    }
                )
            )
        self._transact(items)

    def finish_decision(
        self,
        operation: DecisionOperation,
        result: Mapping[str, Any],
        management: Management,
        published: Mapping[str, Any] | None,
        resolved_request: DeprecationRequest | None = None,
    ) -> None:
        """決裁の成功を確定する。公開承認では Curator が確認した内容を公開版スナップショットに保存する。

        published は公開承認のときだけ指定し、決裁の対象とした版（operation の recordVersion）の
        公開版スナップショットとして保存する。項目は申請スナップショットの値を写し、
        決裁のたびに新しく作る（版は公開中の版より大きいため、上書きされない）。
        廃止では、未解決の廃止依頼があれば resolved_request で同じトランザクションの中で解決済みにする。
        """
        record_pk = self.record_pk(operation["recordId"])
        owner = self._put(
            {**management.item, "pk": record_pk, "sk": "MANAGEMENT"},
            "attribute_not_exists(pk) OR ownerTeam = :team",
            {":team": operation["ownerTeam"]},
        )
        items = [
            owner,
            *self._close(operation, "SUCCEEDED", {"result": result}, operation["recordId"]),
        ]
        if published is not None:
            items.append(
                self._put(
                    {
                        **published,
                        "pk": record_pk,
                        "sk": published_snapshot_sk(operation["recordVersion"]),
                    }
                )
            )
            items.extend(self._reference_index(operation["recordId"], published))
        if resolved_request is not None:
            items.append(self._resolve_deprecation_request(resolved_request))
        self._transact(items)

    def _reference_pk(self, target_id: str) -> str:
        """参照先から、それを関連参照に持つ Agent を引く逆引きのパーティションキー。ソートキーは Agent の ID。"""
        return f"REGISTRY#{self.registry}#REFERENCED#{target_id}"

    def _reference_index(
        self, record_id: str, published: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """公開承認した版の関連参照を、参照先からの逆引きに書く項目（参照先の公開停止・廃止の通知に使う）。

        新しい版で参照をやめても項目は消さない。通知の側で、公開中の版の関連参照を読み直して確かめる。
        """
        references = published.get("relatedReferences")
        targets = {
            reference["recordId"]
            for reference in (references if isinstance(references, list) else [])
            if isinstance(reference, Mapping) and isinstance(reference.get("recordId"), str)
        }
        return [
            self._put(
                {"pk": self._reference_pk(target), "sk": record_id, "recordId": record_id},
                None,
            )
            for target in sorted(targets)
        ]

    def referencing_records(self, target_id: str) -> list[str]:
        """参照先（target_id）を関連参照に持つ版を公開承認した Agent の ID（逆引き）。"""
        params: QueryParams = {
            "TableName": self.table,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": {"S": self._reference_pk(target_id)}},
        }
        records: list[str] = []
        while True:
            response = self._client.query(**params)
            for raw in response.get("Items", []):
                record_id = _deserialize(raw).get("sk")
                if not isinstance(record_id, str):
                    raise ValueError("保存された関連参照の逆引きの形式が正しくありません。")
                records.append(record_id)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return records
            params["ExclusiveStartKey"] = last_key
