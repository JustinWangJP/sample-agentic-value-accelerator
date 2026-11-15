"""Curator の公開管理の照会（GET /api/records/{recordId}/publication）と
管理レコードの検索（GET /api/records/publication）。

公開停止・廃止・廃止依頼の見送りを判断する前に、現在の状態・公開中の版・未解決の廃止依頼・処理中の操作と、
いま実行できる操作（できなければその理由）を1回の照会で返す。決裁 handler が処理する。
SPA の isPublicationView（frontend/src/publication/api.ts）と揃える。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import (
    RECORD_BUSY,
    CurationReads,
    RegistryUnavailable,
    is_managed,
    record_state,
    record_summary,
    require_curator,
)
from registry_portal.domain.catalog import CATALOG_RECORD_TYPES, display_name
from registry_portal.domain.curation import PUBLICATION_ACTIONS, precondition_failure
from registry_portal.domain.drafts import (
    REJECTED_STATUS,
    RECORD_STATUSES,
    OperationKind,
    RecordStatus,
)
from registry_portal.errors import RequestValidationError
from registry_portal.operations import check_next_token, check_record_id

_UNMANAGED = (
    "ポータルで管理されていないレコードは操作できません。管理者に取り込みを依頼してください。"
)

# 管理レコード検索の1ページに並べる上限（AWS の ListRegistryRecords の maxResults）
PUBLICATION_SEARCH_PAGE_SIZE = 20
# 検索の name に指定できる長さ（AWS の ListRegistryRecords のフィルター値と同じ上限）
NAME_QUERY_MAX_LENGTH = 255
_ALLOWED_QUERY_KEYS = {"name", "status", "nextToken"}
# 検索の status で「公開停止中」を表す、ポータル側の語彙
SUSPENDED_QUERY_STATUS: Final = "SUSPENDED"
# 検索の status の閉じた集合。AWS の状態の語彙（domain/drafts.py の RecordStatus、SPA の
# AWS_RECORD_STATUSES と同じ綴り）に SUSPENDED を加えたもの。値は RecordStatus から
# 再スペルせず、一覧（PUBLICATION_SEARCH_STATUSES）も検証エラーの文言もここから組み立てる
PublicationSearchStatus = Literal[RecordStatus, "SUSPENDED"]
PUBLICATION_SEARCH_STATUSES: tuple[PublicationSearchStatus, ...] = (
    *RECORD_STATUSES,
    SUSPENDED_QUERY_STATUS,
)
_STATUS_QUERY_ERROR = (
    f"status は {'・'.join(PUBLICATION_SEARCH_STATUSES)} のいずれかを指定してください。"
)


def _action(failure: str | None) -> dict[str, Any]:
    return {"allowed": failure is None, "message": failure}


class RecordPublication:
    """公開停止・廃止の判断に使う、レコード1件の公開管理の状態。"""

    def __init__(self, reads: CurationReads) -> None:
        self.reads = reads

    def inspect(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        require_curator(caller)
        check_record_id(record_id)
        record = self.reads.require_record(record_id)
        management = self.reads.load_management(record_id)
        operation_id = self.reads.locking_operation_id(record_id)
        request = self.reads.open_deprecation_request(record_id)
        state = record_state(record, management)

        def failure_of(kind: OperationKind) -> str | None:
            if not is_managed(management):
                return _UNMANAGED
            if operation_id is not None:
                return RECORD_BUSY
            return precondition_failure(kind, state)

        return 200, {
            **record_summary(record, management),
            "publishedVersion": state.published_version,
            "suspended": management is not None and management.is_suspended(record),
            "operationId": operation_id,
            "deprecationRequest": request.view() if request else None,
            "actions": {
                action: _action(failure_of(kind)) for action, kind in PUBLICATION_ACTIONS.items()
            },
        }


class PublicationSearch:
    """Curator が公開管理を開く対象を探す、管理下のレコードの一覧。"""

    def __init__(self, reads: CurationReads) -> None:
        self.reads = reads

    def search(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        require_curator(caller)
        if set(query) - _ALLOWED_QUERY_KEYS:
            raise RequestValidationError("一覧には name・status・nextToken のみ指定できます。")
        name = query.get("name")
        if name is not None and not (1 <= len(name) <= NAME_QUERY_MAX_LENGTH):
            raise RequestValidationError(
                f"name は1〜{NAME_QUERY_MAX_LENGTH}文字で指定してください。"
            )
        status = query.get("status")
        if status is not None and status not in PUBLICATION_SEARCH_STATUSES:
            raise RequestValidationError(_STATUS_QUERY_ERROR)
        token = query.get("nextToken")
        check_next_token(token)
        filters: list[dict[str, Any]] = [
            {"name": "recordType", "values": list(CATALOG_RECORD_TYPES)}
        ]
        # SUSPENDED は管理情報の印（suspendedVersion）で判断するため AWS には状態を渡せない。
        # REJECTED は公開停止中のレコードも AWS では REJECTED になるため、ポータル側で除外する
        if status is not None and status != SUSPENDED_QUERY_STATUS:
            filters.append({"name": "status", "values": [status]})
        params: dict[str, Any] = {
            "maxResults": PUBLICATION_SEARCH_PAGE_SIZE,
            "filters": filters,
        }
        if token is not None:
            params["nextToken"] = token
        try:
            page = self.reads.list_records(**params)
            items: list[dict[str, Any]] = []
            for record in page["registryRecords"]:
                management = self.reads.load_management(str(record["recordId"]))
                if not is_managed(management):
                    continue
                suspended = management.is_suspended(record)
                if status == SUSPENDED_QUERY_STATUS and not suspended:
                    continue
                if status == REJECTED_STATUS and suspended:
                    continue
                if name is not None and not _matches_name(record, name):
                    continue
                items.append(
                    {
                        **record_summary(record, management),
                        "suspended": suspended,
                        "publishedVersion": management.published_version(record),
                    }
                )
            return 200, {"items": items, "nextToken": page.get("nextToken")}
        except (BotoCoreError, ClientError, KeyError, TypeError):
            # Registry も補助ストアも読めなければ、空の結果にせず 502 として区別する
            raise RegistryUnavailable() from None


def _matches_name(record: Mapping[str, Any], name: str) -> bool:
    """検索の name は、レコードの name と displayName に対する部分一致（大文字小文字を区別しない）。

    AWS の ListRegistryRecords の name フィルターは一致方法が公式文書に記載がないため、
    ポータル側で絞り込む（README「管理レコードの検索」）。
    """
    needle = name.casefold()
    return needle in str(record["name"]).casefold() or needle in display_name(record).casefold()
