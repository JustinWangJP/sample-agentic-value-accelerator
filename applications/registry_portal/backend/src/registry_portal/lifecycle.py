"""Curator の公開管理の照会（GET /api/records/{recordId}/lifecycle）。

公開停止・廃止・廃止依頼の見送りを判断する前に、現在の状態・公開中の版・未解決の廃止依頼・処理中の操作と、
いま実行できる操作（できなければその理由）を1回の照会で返す。決裁 handler が処理する。
SPA の isLifecycleView（frontend/src/lifecycle/api.ts）と揃える。
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import (
    CuratedRecordNotFound,
    CurationReads,
    DecisionUnavailable,
    RegistryRecordMissing,
    is_managed,
    record_state,
    require_curator,
)
from registry_portal.domain.curation import LIFECYCLE_ACTIONS, precondition_failure
from registry_portal.domain.drafts import RECORD_ID_PATTERN, OperationKind
from registry_portal.errors import RequestValidationError

_UNMANAGED = (
    "ポータルで管理されていないレコードは操作できません。管理者に取り込みを依頼してください。"
)
_BUSY = "別の操作が処理中です。結果が確定してから操作してください。"


def _action(failure: str | None) -> dict[str, Any]:
    return {"allowed": failure is None, "message": failure}


class RecordLifecycle:
    """公開停止・廃止の判断に使う、レコード1件の公開管理の状態。"""

    def __init__(self, reads: CurationReads) -> None:
        self.reads = reads

    def inspect(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        require_curator(caller)
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            record = self.reads.get_record(record_id)
        except RegistryRecordMissing:
            raise CuratedRecordNotFound() from None
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None
        management = self.reads.load_management(record_id)
        lock = self.reads.get_lock(record_id)
        request = self.reads.open_deprecation_request(record_id)
        state = record_state(record, management)

        def failure_of(kind: OperationKind) -> str | None:
            if not is_managed(management):
                return _UNMANAGED
            if lock is not None:
                return _BUSY
            return precondition_failure(kind, state)

        return 200, {
            **self.reads.record_summary(record, management),
            "publishedVersion": state.published_version,
            "suspended": bool(management and management.suspended_version is not None),
            "operationId": lock["operationId"] if lock else None,
            "deprecationRequest": request.view() if request else None,
            "actions": {
                action: _action(failure_of(kind)) for action, kind in LIFECYCLE_ACTIONS.items()
            },
        }
