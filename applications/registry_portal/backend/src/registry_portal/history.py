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
    AUDIT_RESULT,
    HISTORY_FAILED,
    HISTORY_IN_PROGRESS,
    HISTORY_SUCCEEDED,
    HISTORY_UNKNOWN,
    RECORD_ID_PATTERN,
    HistoryState,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.services.draft_store import AuditRecord, DraftStore

# 保存内容の異常（管理情報や操作記録の形式違反）は、空の結果にせず利用者に区別して伝える
_STORED_DATA_INVALID = "保存された操作履歴の形式が正しくありません。管理者に連絡してください。"


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


def _entry(operation: Mapping[str, Any], *, state: HistoryState) -> dict[str, Any]:
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


def _unknown_decider_entry(record_version: str) -> dict[str, Any]:
    """取り込み前から公開されていた版の公開承認。決裁した主体は操作記録に残っていないため「不明」。"""
    return {
        "operationId": None,
        "kind": APPROVE_KIND,
        "operator": None,
        "startedAt": None,
        "targetHash": None,
        "state": HISTORY_UNKNOWN,
        "recordVersion": record_version,
    }


def _settled_state(phases: set[str], operation: Mapping[str, Any]) -> HistoryState:
    """追記記録の段階から操作の結果を決める。RESULT がなければ結果確認中。"""
    if AUDIT_RESULT not in phases:
        return HISTORY_IN_PROGRESS
    return HISTORY_FAILED if "errorCode" in operation else HISTORY_SUCCEEDED


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
            if management is None or management.item.get("managed") is not True:
                raise RecordHistoryNotFound()
            if not privileged and not management.is_owned_by(caller.teams):
                raise ForbiddenError()
            records = self.store.list_audit(record_id)
        except (BotoCoreError, ClientError):
            raise HistoryUnavailable() from None
        except ValueError:
            raise HistoryUnavailable(_STORED_DATA_INVALID) from None
        entries = _entries(records)
        # 取り込み前に公開されていた版は、後でポータルが別の版を公開承認しても履歴から消さない
        adopted = management.adopted_published_version
        if adopted is not None:
            entries.insert(0, _unknown_decider_entry(adopted))
        return 200, {
            "recordId": record_id,
            "ownerTeam": management.owner_team,
            "entries": entries,
        }


def _entries(records: list[AuditRecord]) -> list[dict[str, Any]]:
    """操作 ID ごとに INTENT と RESULT を重ね、開始時刻の順に並べた履歴項目を返す。"""
    operations: dict[str, dict[str, Any]] = {}
    phases: dict[str, set[str]] = {}
    for record in records:
        operations[record.operation_id] = {
            **operations.get(record.operation_id, {}),
            **record.item,
        }
        phases.setdefault(record.operation_id, set()).add(record.phase)
    entries = [
        _entry(operation, state=_settled_state(phases[op_id], operation))
        for op_id, operation in operations.items()
    ]
    return sorted(entries, key=lambda entry: str(entry.get("startedAt") or ""))
