"""廃止依頼の受付（Publisher）と見送り（Curator）。

- POST /api/records/{recordId}/deprecation-requests（管理 handler）: Publisher の廃止依頼
- POST /api/records/{recordId}/deprecation-requests/decline（決裁 handler）: Curator の見送り

廃止依頼は AWS の状態を変えない。補助テーブルに保存し、審査待ちの一覧（GET /api/reviews）で
申請と並べて Curator に表示する。依頼は廃止（決裁 handler の廃止）か見送りでだけ閉じる。
公開停止は依頼を閉じない（README「公開停止・廃止・廃止依頼」）。

どちらの書き込みも共通のロックの外では行わず（ロックがあれば 409）、操作記録（INTENT と RESULT）を
同じトランザクションで追記する。結果は履歴（GET /api/records/{recordId}/history）で Publisher も確認できる。
依頼は Curator 全員に、見送りは所有チームの Publisher 全員に、アプリ内の通知（#15）として同じトランザクションで届ける。
見送りは AWS の状態を変えないため、Registry のイベントからは通知できない。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import (
    RECORD_BUSY,
    CurationReads,
    RecordNotFound,
    RegistryUnavailable,
    display_name,
    is_managed,
    record_state,
    require_curator,
)
from registry_portal.domain.curation import check_deprecatable
from registry_portal.domain.deprecation import (
    RESOLUTION_DECLINED,
    DeprecationRequest,
    parse_decline_reason,
    parse_request_reason,
)
from registry_portal.domain.drafts import (
    DECLINE_DEPRECATION_KIND,
    REQUEST_DEPRECATION_KIND,
    Management,
    OperationKind,
    PrincipalRecord,
    digest,
)
from registry_portal.domain.notifications import (
    CURATOR_AUDIENCE,
    Notification,
    NotificationKind,
    operation_key,
    team_audience,
)
from registry_portal.drafts import require_publisher
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import check_record_id, now, principal_of
from registry_portal.services.notification_store import NotificationStore
from registry_portal.services.supplement_store import (
    SupplementStore,
    InstantOperation,
    RecordLocked,
    StoreConflict,
)

# 依頼・見送りの本文の上限。理由（255文字）と依頼 ID（64文字）に余裕を持たせる
DEPRECATION_BODY_MAX_LENGTH = 1024


class DeprecationConflict(ApiError):
    """依頼の状態や別の操作と衝突した。最新の状態を確認してから再度操作する。"""

    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "レコードの状態が変わったため実行できません。最新の状態を確認してください。"


@contextmanager
def _writing(conflict: str) -> Iterator[None]:
    """廃止依頼の書き込みの失敗を API エラーに変換する。

    ロック中は RECORD_BUSY、依頼の条件付き書き込みの衝突は conflict の理由で 409、
    補助ストアに書けなければ 502 にする。
    """
    try:
        yield
    except RecordLocked:
        raise DeprecationConflict(RECORD_BUSY) from None
    except StoreConflict:
        raise DeprecationConflict(conflict) from None
    except (BotoCoreError, ClientError):
        raise RegistryUnavailable() from None


def _audit(
    kind: OperationKind,
    principal: PrincipalRecord,
    management: Management,
    record_id: str,
    reason: str,
    at: str,
) -> InstantOperation:
    return {
        "operationId": digest([kind, record_id, principal, at]),
        "kind": kind,
        "principal": principal,
        "startedAt": at,
        "ownerTeam": management.owner_team,
        "recordId": record_id,
        "reason": reason,
    }


def _in_app_notification(
    audit: InstantOperation,
    audience: str,
    kind: NotificationKind,
    record_name: str | None,
) -> Notification:
    """操作記録と同じトランザクションで書くアプリ内の通知。出来事は操作 ID で表す。"""
    return Notification.create(
        operation_key(audit["operationId"]),
        audience,
        kind,
        audit["recordId"],
        record_name,
        audit["startedAt"],
        reason=audit["reason"],
    )


class DeprecationRequests:
    """Publisher の廃止依頼の受付と、Curator の見送り。所有チームの検査は申請と同じ規則（設計書「ロールと所有」）。"""

    def __init__(
        self, reads: CurationReads, store: SupplementStore, notifications: NotificationStore
    ) -> None:
        self.reads = reads
        self.store = store
        self.notifications = notifications

    def _managed(self, record_id: str) -> Management:
        management = self.reads.load_management(record_id)
        if not is_managed(management):
            raise RecordNotFound("ポータルで管理されていないレコードの廃止依頼は扱えません。")
        return management

    def create(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """廃止依頼を保存する。対象の所有チームを所属チームに持つ Publisher だけが依頼できる。"""
        require_publisher(caller)
        check_record_id(record_id)
        if set(body) != {"reason"}:
            raise RequestValidationError(
                "reason を指定してください。ステータスは API ごとに決まるため指定できません。"
            )
        try:
            reason = parse_request_reason(body["reason"])
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        # 対象の所有チームを所属チームに持つ Publisher だけが依頼できる。所属チームのない
        # Publisher は所有チームに含まれないため 403 になる（設計書「ロールと所有」）。
        # AWS のレコードを読む前に検査し、所有チームに属さない呼び出し元にはレコードの有無や
        # 管理状態を見せない。管理情報がない・管理下にないレコードも、下書きの操作と同じく 403
        management = self.reads.load_management(record_id)
        if management is None or not management.is_owned_by(caller.teams):
            raise ForbiddenError()
        record = self.reads.require_record(record_id)
        # 廃止できない状態（処理中・廃止済み）は依頼も受け付けない。判定は廃止の前提と共通にする
        try:
            check_deprecatable(record_state(record, management))
        except ValueError as error:
            raise DeprecationConflict(str(error)) from None
        principal = principal_of(caller)
        at = now()
        request = DeprecationRequest(
            request_id=digest(["DEPRECATION_REQUEST", self.store.registry, record_id, at]),
            record_id=record_id,
            reason=reason,
            requested_by=principal,
            requested_at=at,
        )
        audit = _audit(REQUEST_DEPRECATION_KIND, principal, management, record_id, reason, at)
        notification = _in_app_notification(
            audit, CURATOR_AUDIENCE, "DEPRECATION_REQUESTED", display_name(record)
        )
        with _writing(
            "このレコードには未解決の廃止依頼があります。Curator の対応をお待ちください。"
        ):
            self.store.save_deprecation_request(
                request, audit, self.notifications.notification_put(notification)
            )
        return 200, request.view()

    def decline(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """未解決の廃止依頼を、理由を付けて見送る。レコードの状態は変えない（公開停止中ならそのまま）。

        画面で確認した依頼（requestId）と現在の依頼が一致するときだけ見送る。
        """
        require_curator(caller)
        check_record_id(record_id)
        if set(body) != {"requestId", "reason"} or not isinstance(body["requestId"], str):
            raise RequestValidationError("requestId と reason を指定してください。")
        try:
            reason = parse_decline_reason(body["reason"])
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        management = self._managed(record_id)
        request = self.reads.open_deprecation_request(record_id)
        if request is None or request.request_id != body["requestId"]:
            raise DeprecationConflict(
                "見送る廃止依頼が見つかりません。すでに対応済みか、新しい依頼に置き換わっています。"
            )
        principal = principal_of(caller)
        at = now()
        declined = request.resolve(RESOLUTION_DECLINED, reason, principal, at)
        audit = _audit(DECLINE_DEPRECATION_KIND, principal, management, record_id, reason, at)
        # 見送りは Registry を読まないため、通知にはレコードの名前を含めず record ID で示す
        notification = _in_app_notification(
            audit, team_audience(management.owner_team), "DEPRECATION_DECLINED", None
        )
        with _writing("廃止依頼の状態が変わりました。最新の状態を確認してください。"):
            self.store.decline_deprecation_request(
                declined, audit, self.notifications.notification_put(notification)
            )
        return 200, declined.view()
