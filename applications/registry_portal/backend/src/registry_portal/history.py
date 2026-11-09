"""操作と審査の履歴（GET /api/records/{recordId}/history）。

補助テーブルの追記記録（AUDIT 項目）だけを正本とし、履歴は保存した操作記録から組み立てる。
閲覧はロールと所有チームで検査する（設計書「ロールと所有」）:
Curator と Admin はすべてのレコード、Publisher は所有チームのレコードだけが読める。
結果確認中の決裁の中身は、決裁した Curator 本人にだけ返す（ADR-0010）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    AUDIT_INTENT,
    AUDIT_RESULT,
    DECISION_KINDS,
    HISTORY_FAILED,
    HISTORY_SUCCEEDED,
    HISTORY_UNKNOWN,
    HISTORY_VERIFYING,
    OPERATION_KINDS,
    RECORD_ID_PATTERN,
    HistoryState,
    PrincipalRecord,
    is_principal,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import principal_of
from registry_portal.services.draft_store import AppendedRecord, DraftStore

# 保存内容の異常（管理情報や操作記録の形式違反）は、空の結果にせず利用者に区別して伝える
_STORED_DATA_INVALID = "保存された操作履歴の形式が正しくありません。管理者に連絡してください。"

# 操作記録にあれば履歴項目に含める項目。決裁の理由と対象の版は決裁の操作だけが持つ
_OPTIONAL_FIELDS = ("awsRequestId", "errorCode", "reason", "recordVersion")


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


def _target_hash(operation: Mapping[str, Any]) -> str | None:
    """操作の対象の内容ハッシュ（またはスナップショットのハッシュ）。

    編集・申請・決裁は、操作の時点で対象とした内容ハッシュ（決裁はスナップショットのハッシュも持つ）。
    作成には操作の時点の内容がないため、作成した内容の内容ハッシュ（成功した結果の contentHash）を使う。
    作成の結果が確定していなければ内容ハッシュはまだなく None になる。requestHash は同じ
    Idempotency-Key の依頼を照合するための値で、内容ハッシュではないため使わない。
    """
    for key in ("expectedContentHash", "snapshotHash"):
        value = operation.get(key)
        if isinstance(value, str):
            return value
    result = operation.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("contentHash"), str):
        return str(result["contentHash"])
    return None


def _entry(op_id: str, operation: Mapping[str, Any], *, state: HistoryState) -> dict[str, Any]:
    """INTENT と RESULT を重ねた操作1件を、API が返す履歴1項目に組み立てる。

    必須の項目（operationId・kind・principal・startedAt・targetHash・state）は値がなくても
    null として必ず返し、SPA の検査（frontend/src/drafts/api.ts の isHistoryEntry）と揃える。
    """
    entry: dict[str, Any] = {
        "operationId": op_id,
        "kind": operation["kind"],
        "principal": operation["principal"],
        "startedAt": operation["startedAt"],
        "targetHash": _target_hash(operation),
        "state": state,
    }
    for key in _OPTIONAL_FIELDS:
        if isinstance(operation.get(key), str):
            entry[key] = operation[key]
    return entry


def _withheld(entry: Mapping[str, Any]) -> dict[str, Any]:
    """結果確認中の決裁を、決裁した Curator 以外に返す形。

    ADR-0010 のとおり、操作の主体以外には結果確認中の決裁の中身（理由・対象のハッシュと版・
    AWS request ID）と確定の契機（操作 ID）を渡さない。決裁が進行中であることと主体・開始時刻だけを返す。
    """
    return {
        "operationId": None,
        "kind": entry["kind"],
        "principal": entry["principal"],
        "startedAt": entry["startedAt"],
        "targetHash": None,
        "state": entry["state"],
        "withheld": True,
    }


def _visible_to(entry: Mapping[str, Any], viewer: PrincipalRecord) -> dict[str, Any]:
    if (
        entry["kind"] in DECISION_KINDS
        and entry["state"] == HISTORY_VERIFYING
        and entry["principal"] != viewer
    ):
        return _withheld(entry)
    return dict(entry)


def _unknown_decider_entry(record_version: str) -> dict[str, Any]:
    """取り込み前から公開されていた版の公開承認。決裁した主体は操作記録に残っていないため「不明」。"""
    return {
        "operationId": None,
        "kind": APPROVE_KIND,
        "principal": None,
        "startedAt": None,
        "targetHash": None,
        "state": HISTORY_UNKNOWN,
        "recordVersion": record_version,
    }


def _settled_state(phases: set[str], operation: Mapping[str, Any]) -> HistoryState:
    """追記記録の段階から操作の結果を決める。RESULT がなければ結果確認中。"""
    if AUDIT_RESULT not in phases:
        return HISTORY_VERIFYING
    return HISTORY_FAILED if "errorCode" in operation else HISTORY_SUCCEEDED


def _check_operation(phases: set[str], operation: Mapping[str, Any]) -> None:
    """操作記録の組が契約どおりか検査する。INTENT がない、または必須の項目の型が違えば ValueError。"""
    if (
        AUDIT_INTENT not in phases
        or operation.get("kind") not in OPERATION_KINDS
        or not is_principal(operation.get("principal"))
        or not isinstance(operation.get("startedAt"), str)
    ):
        raise ValueError("保存された操作記録の形式が正しくありません。")


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
            if management is None or not management.is_managed:
                raise RecordHistoryNotFound()
            if not privileged and not management.is_owned_by(caller.teams):
                raise ForbiddenError()
            records = self.store.list_appended_records(record_id)
            entries = _entries(records)
        except (BotoCoreError, ClientError):
            raise HistoryUnavailable() from None
        except ValueError:
            raise HistoryUnavailable(_STORED_DATA_INVALID) from None
        viewer = principal_of(caller)
        visible = [_visible_to(entry, viewer) for entry in entries]
        # 取り込み前に公開されていた版は、後でポータルが別の版を公開承認しても履歴から消さない
        adopted = management.adopted_published_version
        if adopted is not None:
            visible.insert(0, _unknown_decider_entry(adopted))
        return 200, {
            "recordId": record_id,
            "ownerTeam": management.owner_team,
            "entries": visible,
        }


def _entries(records: list[AppendedRecord]) -> list[dict[str, Any]]:
    """操作 ID ごとに INTENT と RESULT を重ね、開始時刻の順に並べた履歴項目を返す。

    形式の崩れた操作記録は捨てずに ValueError とし、保存内容の異常として利用者に伝える。
    """
    operations: dict[str, dict[str, Any]] = {}
    phases: dict[str, set[str]] = {}
    for record in records:
        operations[record.operation_id] = {
            **operations.get(record.operation_id, {}),
            **record.item,
        }
        phases.setdefault(record.operation_id, set()).add(record.phase)
    entries = []
    for op_id, operation in operations.items():
        _check_operation(phases[op_id], operation)
        entries.append(_entry(op_id, operation, state=_settled_state(phases[op_id], operation)))
    return sorted(entries, key=lambda entry: str(entry["startedAt"]))
