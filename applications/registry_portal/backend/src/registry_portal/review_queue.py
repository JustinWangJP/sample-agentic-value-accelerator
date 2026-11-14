"""審査待ちの一覧（GET /api/reviews）と審査の詳細（GET /api/reviews/{recordId}）。

一覧には、AWS の承認待ち（申請）と、補助テーブルの未解決の廃止依頼を並べる（設計書「API」）。
種別（reviewKind）ごとに項目の形が異なる。SPA の isReviewItem（frontend/src/reviews/api.ts）と揃える。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import (
    CurationReads,
    PendingApprovalNotFound,
    RegistryRecordMissing,
    RegistryUnavailable,
    record_summary,
    require_curator,
)
from registry_portal.domain.curation import REVIEW_DEPRECATION_REQUEST, REVIEW_SUBMISSION
from registry_portal.domain.deprecation import DeprecationRequest
from registry_portal.domain.drafts import PENDING_APPROVAL_STATUS
from registry_portal.domain.submissions import recompute_snapshot_hash, snapshot_fields
from registry_portal.errors import RequestValidationError
from registry_portal.operations import check_next_token, check_record_id

# 1ページに並べる申請の上限（AWS の ListRegistryRecords の maxResults）
REVIEW_PAGE_SIZE = 20


def _snapshot_view(submission: Mapping[str, Any]) -> dict[str, Any]:
    """画面に返す申請スナップショット。保存用のキーを除き、ハッシュを添える。"""
    return {**snapshot_fields(submission), "snapshotHash": submission.get("snapshotHash")}


class ReviewQueue:
    """Curator の審査待ちの一覧と、申請の審査の詳細。"""

    def __init__(self, reads: CurationReads) -> None:
        self.reads = reads

    def list_reviews(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        """審査待ちの一覧。AWS の承認待ちを正本とし、そこに補助情報を結合する。

        未解決の廃止依頼は補助テーブルが正本で、AWS のページとは別に数が決まる。ページごとに重複して
        表示しないよう、最初のページ（nextToken なし）にだけ古い順に並べる。
        """
        require_curator(caller)
        if set(query) - {"nextToken"}:
            raise RequestValidationError("一覧には nextToken のみ指定できます。")
        token = query.get("nextToken")
        check_next_token(token)
        params: dict[str, Any] = {
            "maxResults": REVIEW_PAGE_SIZE,
            "filters": [{"name": "status", "values": [PENDING_APPROVAL_STATUS]}],
        }
        if token is not None:
            params["nextToken"] = token
        try:
            response = self.reads.list_records(**params)
            items = [self._submission_item(record) for record in response["registryRecords"]]
            if token is None:
                items = [*self._deprecation_request_items(), *items]
            return 200, {"items": items, "nextToken": response.get("nextToken")}
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None

    def _submission_item(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """AWS の承認待ちレコード1件に、申請の補助情報を結合する。"""
        record_id = str(record["recordId"])
        management = self.reads.load_management(record_id)
        state, submission_id, submission = self.reads.active_submission(record_id, management)
        return {
            "reviewKind": REVIEW_SUBMISSION,
            **record_summary(record, management),
            "submissionState": state,
            "submissionId": submission_id,
            "snapshotHash": submission.get("snapshotHash") if submission else None,
            "reason": submission.get("reason") if submission else None,
            "submittedAt": submission.get("submittedAt") if submission else None,
            "submittedBy": submission.get("submittedBy") if submission else None,
            "invalidated": bool(submission.get("invalidated")) if submission else False,
            "operationId": self.reads.locking_operation_id(record_id),
        }

    def _deprecation_request_items(self) -> list[dict[str, Any]]:
        """未解決の廃止依頼を、申請と並べる項目にする。対象のレコードが削除済みなら表示しない。"""
        items: list[dict[str, Any]] = []
        for request in self.reads.open_deprecation_requests():
            try:
                record = self.reads.get_record(request.record_id)
            except RegistryRecordMissing:
                continue
            items.append(self._deprecation_request_item(record, request))
        return items

    def _deprecation_request_item(
        self, record: Mapping[str, Any], request: DeprecationRequest
    ) -> dict[str, Any]:
        management = self.reads.load_management(request.record_id)
        return {
            "reviewKind": REVIEW_DEPRECATION_REQUEST,
            **record_summary(record, management),
            "requestId": request.request_id,
            "reason": request.reason,
            "requestedAt": request.requested_at,
            "requestedBy": dict(request.requested_by),
            # 公開停止済みで、廃止するか見送るかの判断を待っている
            "suspended": management is not None and management.is_suspended(record),
            "operationId": self.reads.locking_operation_id(request.record_id),
        }

    def review(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        """審査対象の詳細。申請のスナップショットと、差分の比較対象（公開中の版）を返す。

        未解決の廃止依頼があれば添える。依頼を出した Publisher が再申請した場合の見落としを防ぐ。
        """
        require_curator(caller)
        check_record_id(record_id)
        try:
            record = self.reads.get_record(record_id)
        except RegistryRecordMissing:
            raise PendingApprovalNotFound() from None
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        if record["status"] != PENDING_APPROVAL_STATUS:
            raise PendingApprovalNotFound()
        management = self.reads.load_management(record_id)
        state, submission_id, submission = self.reads.active_submission(record_id, management)
        published_state, published = self.reads.published_snapshot(record_id, management, record)
        request = self.reads.open_deprecation_request(record_id)
        return 200, {
            **record_summary(record, management),
            "description": record.get("description", ""),
            "publishedVersion": management.published_version(record) if management else None,
            "submissionState": state,
            "submissionId": submission_id,
            "snapshotHash": submission.get("snapshotHash") if submission else None,
            # 保存後にスナップショットが書き換えられていれば偽。決裁は 409 で拒否される
            "snapshotIntact": (
                recompute_snapshot_hash(submission) == submission.get("snapshotHash")
                if submission
                else False
            ),
            "invalidated": bool(submission.get("invalidated")) if submission else False,
            "snapshot": _snapshot_view(submission) if submission else None,
            "publishedState": published_state,
            "published": published,
            "deprecationRequest": request.view() if request else None,
        }
