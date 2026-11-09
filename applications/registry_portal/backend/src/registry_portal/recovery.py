"""Admin の回復操作。結果確認中のまま止まった操作を、Registry を再取得した結果から確定する。

回復は決裁 handler が受け持つ（設計書「最小インフラとセキュリティ」の決裁 Lambda）。手順は次のとおり。

1. レコード共通のロックを持つ操作を特定する（操作 ID を利用者から受け取らない）
2. Registry を再取得し、観測できる状態だけから操作を確定する。AWS の書き込み API は呼び直さない
3. それでも確定できず、Admin が強制解除を指定したときだけ、操作を失敗として確定してロックを解除する

ロックは期限で解除しない。実行中の操作に重ねて回復しないよう、開始から一定時間が過ぎた操作だけを対象にする。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.curation import RecordCuration
from registry_portal.domain.drafts import (
    IN_FLIGHT_STATUSES,
    RECORD_ID_PATTERN,
    RECOVERY_RECONCILED,
    RECOVERY_RELEASED,
    RecoveryRecord,
    StoredOperation,
    is_decision_operation,
)
from registry_portal.drafts import RecordDrafts
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import principal_of, released_message
from registry_portal.services.draft_store import DraftStore, StoreConflict

# 回復の理由の上限。AWS には送らずポータルの操作記録にだけ残すため、決裁理由（domain/curation.py の
# RECOVERY_REASON_MAX_LENGTH）とは別に定義する。SPA の RECOVERY_REASON_MAX_LENGTH（recovery/api.ts）と揃える
RECOVERY_REASON_MAX_LENGTH = 255
# 開始からこの時間が過ぎるまで、操作は実行中の Lambda が続けている可能性があるため回復しない。
# Lambda のタイムアウト（30秒）と再試行に十分な余裕を持たせた値
MIN_OPERATION_AGE = timedelta(minutes=5)
RELEASED_ERROR_CODE = "ADMIN_RELEASED"
RECOVERY_BODY_MAX_LENGTH = 2048
_LOCK_WITHOUT_OPERATION = (
    "ロックに対応する操作が見つかりません。保存内容の異常として管理者に連絡してください。"
)


class NoRecoverableOperation(ApiError):
    """レコードに、回復の対象になる未確定の操作がない。"""

    status = 409
    code = "NO_RECOVERABLE_OPERATION"
    default_message = "このレコードに結果確認中の操作はありません。"


class OperationStillRunning(ApiError):
    """開始から間もない操作は、実行中の可能性があるため回復できない。"""

    status = 409
    code = "OPERATION_STILL_RUNNING"
    default_message = (
        "操作の開始から5分が過ぎていないため回復できません。時間をおいて再度お試しください。"
    )


class RecordStillProcessing(ApiError):
    """AWS が書き込みを処理中のレコードは、結果が出る前に強制解除しない。"""

    status = 409
    code = "RECORD_STILL_PROCESSING"
    default_message = "Registry がレコードを処理中のため強制解除できません。処理が終わってから再度お試しください。"


class RecoveryUnavailable(ApiError):
    status = 502
    code = "REGISTRY_UNAVAILABLE"
    default_message = (
        "Registry または補助情報を取得できませんでした。時間をおいて再度お試しください。"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_admin(caller: Caller) -> None:
    if Role.ADMIN not in caller.roles:
        raise ForbiddenError("回復操作は Admin のみ行えます。")


class OperationRecovery:
    """結果確認中の操作の照会と、Registry の再取得による回復。"""

    def __init__(
        self, registry: str, store: DraftStore, curation: RecordCuration, drafts: RecordDrafts
    ) -> None:
        self.registry = registry
        self.store = store
        self._curation = curation
        self._drafts = drafts

    def inspect(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        """レコードのロックを持つ操作の概要。AWS は呼ばず、操作の中身（理由・ハッシュ）は返さない。"""
        require_admin(caller)
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            lock = self.store.get_lock(record_id)
            operation = None if lock is None else self.store.get_operation(str(lock["operationId"]))
        except (BotoCoreError, ClientError):
            raise RecoveryUnavailable() from None
        if lock is not None and operation is None:
            raise RecoveryUnavailable(_LOCK_WITHOUT_OPERATION)
        if operation is None:
            return 200, {"recordId": record_id, "operation": None}
        return 200, {
            "recordId": record_id,
            "operation": {
                "kind": operation["kind"],
                "operationState": "VERIFYING",
                "principal": operation["principal"],
                "startedAt": operation["startedAt"],
                "recoverable": self._is_idle(operation),
            },
        }

    def recover(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        require_admin(caller)
        reason, release = _parse_body(body)
        operation = self._locked_operation(record_id)
        self._check_age(operation)
        recovery: RecoveryRecord = {
            "mode": RECOVERY_RECONCILED,
            "by": principal_of(caller),
            "at": _utcnow().isoformat(),
            "reason": reason,
        }
        operation["recovery"] = recovery
        status, outcome = self._settle(operation)
        if outcome["operationState"] != "VERIFYING":
            return status, _summary(record_id, outcome, recovery)
        if not release:
            del operation["recovery"]
            return 202, _summary(record_id, outcome, None)
        return self._release(operation, recovery, record_id)

    def _release(
        self, operation: StoredOperation, recovery: RecoveryRecord, record_id: str
    ) -> tuple[int, dict[str, Any]]:
        """確定できない操作を、Admin の指定で失敗として確定しロックを解除する。"""
        released: RecoveryRecord = {**recovery, "mode": RECOVERY_RELEASED}
        operation["recovery"] = released
        try:
            # Registry の状態を見られないまま、結果を確かめずにロックを外さない
            status = self._curation.observe_record_status(operation["recordId"])
        except ApiError:
            raise RecoveryUnavailable() from None
        if status in IN_FLIGHT_STATUSES:
            raise RecordStillProcessing()
        try:
            self.store.abort(operation, RELEASED_ERROR_CODE)
        except (BotoCoreError, ClientError, StoreConflict):
            raise RecoveryUnavailable() from None
        return 200, _summary(
            record_id,
            {
                "operationState": "FAILED",
                "message": released_message(recovery["reason"]),
            },
            released,
        )

    def _settle(self, operation: StoredOperation) -> tuple[int, dict[str, Any]]:
        if is_decision_operation(operation):
            status, outcome = self._curation.settle(operation, allow_reissue=False)
        else:
            status, outcome = self._drafts.settle_by_observation(operation)
        return status, dict(outcome)

    def _locked_operation(self, record_id: str) -> StoredOperation:
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            lock = self.store.get_lock(record_id)
            if lock is None:
                raise NoRecoverableOperation()
            operation = self.store.get_operation(str(lock["operationId"]))
        except (BotoCoreError, ClientError):
            raise RecoveryUnavailable() from None
        if operation is None:
            raise RecoveryUnavailable(_LOCK_WITHOUT_OPERATION)
        return operation

    @staticmethod
    def _is_idle(operation: StoredOperation) -> bool:
        return _utcnow() - datetime.fromisoformat(operation["startedAt"]) >= MIN_OPERATION_AGE

    def _check_age(self, operation: StoredOperation) -> None:
        if not self._is_idle(operation):
            raise OperationStillRunning()


_SETTLED_MESSAGE = "Registry の状態から操作の成功を確認し、ロックを解除しました。"


def _summary(
    record_id: str, outcome: Mapping[str, Any], recovery: RecoveryRecord | None
) -> dict[str, Any]:
    """回復の応答。操作の中身（スナップショット・下書きの内容）は含めず、結果の要約だけを返す。"""
    return {
        "recordId": record_id,
        "operationState": outcome["operationState"],
        "message": outcome.get("message", _SETTLED_MESSAGE),
        "recovery": recovery,
    }


def _parse_body(body: Mapping[str, Any]) -> tuple[str, bool]:
    reason = body.get("reason")
    release = body.get("release", False)
    if (
        set(body) - {"reason", "release"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > RECOVERY_REASON_MAX_LENGTH
        or not isinstance(release, bool)
    ):
        raise RequestValidationError(
            f"reason（1〜{RECOVERY_REASON_MAX_LENGTH}文字）と、任意の release（真偽値）を指定してください。"
        )
    return reason, release
