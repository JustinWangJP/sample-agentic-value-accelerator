"""操作と審査の履歴（GET /api/records/{recordId}/history）。

補助テーブルの追記記録（AUDIT 項目）だけを正本とし、履歴は保存した操作記録から組み立てる。
閲覧はロールと所有チームで検査する（設計書「ロールと所有」）:
Curator と Admin はすべてのレコード、Publisher は所有チームのレコードだけが読める。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    RECORD_ID_PATTERN,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.services.draft_store import DraftStore

# 追記記録のソートキー（AUDIT#<操作 ID>#INTENT / AUDIT#<操作 ID>#RESULT）の要素数
_AUDIT_SK_PARTS = 3


class HistoryUnavailable(ApiError):
    status = 502
    code = "HISTORY_UNAVAILABLE"
    default_message = "操作履歴を取得できませんでした。時間をおいて再度お試しください。"


class RecordHistoryNotFound(ApiError):
    status = 404
    code = "RECORD_NOT_FOUND"
    default_message = "指定された Registry レコードの操作履歴はありません。"


def _is_privileged(caller: Caller) -> bool:
    """Curator と Admin は、所有チームによらずすべての履歴を読める。"""
    return Role.CURATOR in caller.roles or Role.ADMIN in caller.roles


def _entry(operation: Mapping[str, Any], *, state: str) -> dict[str, Any]:
    """追記記録1件分（または組）を、API が返す履歴1項目に組み立てる。"""
    entry: dict[str, Any] = {
        "operationId": operation.get("operationId"),
        "kind": operation.get("kind"),
        "operator": operation.get("principal"),
        "startedAt": operation.get("startedAt"),
        "targetHash": operation.get("expectedContentHash")
        or operation.get("snapshotHash")
        or operation.get("requestHash"),
        "state": state,
    }
    for key in ("awsRequestId", "errorCode", "reason", "recordVersion"):
        if key in operation:
            entry[key] = operation[key]
    return {key: value for key, value in entry.items() if value is not None}


def _unknown_approver_entry() -> dict[str, Any]:
    """取り込み前から公開されていた版の承認。承認者は操作記録に残っていないため「不明」。"""
    return {
        "operationId": None,
        "kind": APPROVE_KIND,
        "operator": None,
        "startedAt": None,
        "targetHash": None,
        "state": "UNKNOWN",
    }


class RecordHistory:
    """補助ストアの追記記録から、レコード単位の操作と審査の履歴を組み立てる。"""

    def __init__(self, store: DraftStore) -> None:
        self.store = store

    def history(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        privileged = _is_privileged(caller)
        if not privileged and Role.PUBLISHER not in caller.roles:
            raise ForbiddenError("操作履歴は Publisher・Curator・Admin が閲覧できます。")
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        if not privileged and not caller.teams:
            raise ForbiddenError("操作履歴の閲覧には所属チームが必要です。")
        try:
            management = self.store.get_management(record_id)
        except (BotoCoreError, ClientError):
            raise HistoryUnavailable() from None
        except ValueError:
            raise HistoryUnavailable(
                "保存された管理情報の形式が正しくありません。管理者に連絡してください。"
            ) from None
        if management is None or management.item.get("managed") is not True:
            raise RecordHistoryNotFound()
        if not privileged and not management.is_owned_by(caller.teams):
            raise ForbiddenError()
        entries = self._entries(record_id)
        if management.item.get("publishedVersion") is not None and not any(
            entry["kind"] == APPROVE_KIND and entry["state"] == "SUCCEEDED" for entry in entries
        ):
            entries.insert(0, _unknown_approver_entry())
        return 200, {
            "recordId": record_id,
            "ownerTeam": management.owner_team,
            "entries": entries,
        }

    def _entries(self, record_id: str) -> list[dict[str, Any]]:
        try:
            items = self.store.list_audit(record_id)
        except (BotoCoreError, ClientError):
            raise HistoryUnavailable() from None
        operations: dict[str, dict[str, Any]] = {}
        settled: dict[str, bool] = {}
        for item in items:
            parts = str(item.get("sk", "")).split("#")
            if len(parts) != _AUDIT_SK_PARTS:
                continue
            op_id, phase = parts[1], parts[2]
            merged = {**operations.get(op_id, {}), **item}
            operations[op_id] = merged
            if phase == "RESULT":
                settled[op_id] = "errorCode" not in merged
        entries = [
            _entry(
                operation,
                state=(
                    "SUCCEEDED"
                    if settled.get(op_id) is True
                    else "FAILED" if settled.get(op_id) is False else "IN_PROGRESS"
                ),
            )
            for op_id, operation in operations.items()
        ]
        return sorted(entries, key=lambda entry: str(entry.get("startedAt") or ""))
