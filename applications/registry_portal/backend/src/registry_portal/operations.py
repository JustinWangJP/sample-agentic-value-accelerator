"""下書きの操作（drafts.py）と決裁の操作（curation.py）に共通する、API 層の補助。

AWS のエラーの分類、Idempotency-Key の検査、操作の応答の組み立てを一か所にまとめる。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from registry_portal.auth import Caller
from registry_portal.domain.drafts import (
    OperationRecord,
    OperationState,
    OperationStatus,
    RECORD_ID_PATTERN,
    RECOVERY_RELEASED,
    PrincipalRecord,
)
from registry_portal.errors import RequestValidationError

# AWS が入力や設定を理由に処理しなかったと確定できるエラー。利用者が入力や設定を直して再実行する。
# ConflictException の扱いは API ごとに異なるため、ここには含めない
AWS_REJECTED_ERRORS = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "ServiceQuotaExceededException",
    }
)
# AWS が混雑を理由に処理せず拒否したエラー。入力は正しいため、時間をおいて同じ内容で再実行する
AWS_THROTTLED_ERRORS = frozenset({"ThrottlingException"})

_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


def check_idempotency_key(key: str) -> None:
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise RequestValidationError(
            "Idempotency-Key は1〜128文字の英数字・_・- で指定してください。"
        )


def check_record_id(record_id: str) -> None:
    """path で受け取った Registry レコード ID の形式を検査する。誤っていれば 400。"""
    if not RECORD_ID_PATTERN.fullmatch(record_id):
        raise RequestValidationError("Registry レコード ID の形式が正しくありません。")


def principal_of(caller: Caller) -> PrincipalRecord:
    """操作記録に残す主体。検証済み JWT の issuer と subject だけを使う（設計書「補助テーブルに保存するもの」）。"""
    return {"issuer": caller.principal.issuer, "subject": caller.principal.subject}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_request_id(operation: OperationRecord, response: Mapping[str, Any]) -> None:
    """AWS の応答（エラー応答を含む）の request ID を操作記録に残す（設計書「操作記録」）。"""
    request_id = response.get("ResponseMetadata", {}).get("RequestId")
    if isinstance(request_id, str) and request_id:
        operation["awsRequestId"] = request_id


def operation_status(
    operation: Mapping[str, Any], state: OperationState, message: str
) -> OperationStatus:
    """結果確認中、または失敗が確定した操作の応答。回復操作で確定した操作には回復結果を添える。"""
    recovery = operation.get("recovery")
    if state == "FAILED" and recovery is not None and recovery["mode"] == RECOVERY_RELEASED:
        message = released_message(recovery["reason"])
    status: OperationStatus = {
        "operationId": operation["operationId"],
        "operationState": state,
        "message": message,
    }
    if recovery is not None:
        status["recovery"] = recovery
    return status


def released_message(reason: str) -> str:
    """Admin が結果を確定できないまま強制解除した操作の、失敗のメッセージ。"""
    return (
        f"Admin が結果を確認できないまま操作を強制解除しました（理由: {reason}）。"
        "Registry の状態を確認してから再実行してください。"
    )
