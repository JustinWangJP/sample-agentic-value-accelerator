"""廃止依頼の受付（Publisher）と見送り（Curator）。

- POST /api/records/{recordId}/deprecation-requests（管理 handler）: Publisher の廃止依頼
- POST /api/records/{recordId}/deprecation-requests/decline（決裁 handler）: Curator の見送り

廃止依頼は AWS の状態を変えない。補助テーブルに保存し、審査待ちの一覧（GET /api/reviews）で
申請と並べて Curator に表示する。依頼は廃止（決裁 handler の廃止）か見送りでだけ閉じる。
公開停止は依頼を閉じない（README「公開停止・廃止・廃止依頼」）。

どちらの書き込みも共通のロックの外では行わず（ロックがあれば 409）、操作記録（INTENT と RESULT）を
同じトランザクションで追記する。結果は履歴（GET /api/records/{recordId}/history）で Publisher も確認できる。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import require_curator
from registry_portal.domain.deprecation import (
    RESOLUTION_DECLINED,
    DeprecationRequest,
    parse_decline_reason,
    parse_request_reason,
)
from registry_portal.domain.drafts import (
    DECLINE_DEPRECATION_KIND,
    DEPRECATED_STATUS,
    IN_FLIGHT_STATUSES,
    RECORD_ID_PATTERN,
    REQUEST_DEPRECATION_KIND,
    Management,
    OperationKind,
    PrincipalRecord,
    digest,
)
from registry_portal.drafts import require_publisher
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import now, principal_of
from registry_portal.services.draft_store import DraftStore, InstantOperation, StoreConflict

# 依頼・見送りの本文の上限。理由（255文字）と依頼 ID（64文字）に余裕を持たせる
DEPRECATION_BODY_MAX_LENGTH = 1024

# 補助ストアの書き込みで、ロックの確認にあたる項目の位置（DraftStore の save / decline と揃える）
_LOCK_ITEM = 1
_BUSY = "別の操作が処理中です。結果が確定してから再度お試しください。"


class DeprecationUnavailable(ApiError):
    status = 502
    code = "REGISTRY_UNAVAILABLE"
    default_message = (
        "Registry または補助情報を取得できませんでした。時間をおいて再度お試しください。"
    )


class DeprecationRecordNotFound(ApiError):
    """廃止依頼の対象の Registry レコードが見つからない、またはポータルの管理下にない。"""

    status = 404
    code = "RECORD_NOT_FOUND"
    default_message = "指定された Registry レコードは見つかりません。"


class DeprecationConflict(ApiError):
    """依頼の状態や別の操作と衝突した。最新の状態を確認してから再度操作する。"""

    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "レコードの状態が変わったため実行できません。最新の状態を確認してください。"


class RegistryRecordReader(Protocol):
    """廃止依頼が使う Registry 管理 API（agent-registry-control）の読み取り。"""

    def get_registry_record(self, *, registryId: str, recordId: str) -> Mapping[str, Any]: ...


def _check_record_id(record_id: str) -> None:
    if not RECORD_ID_PATTERN.fullmatch(record_id):
        raise RequestValidationError("Registry レコード ID の形式が正しくありません。")


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


class DeprecationRequests:
    """Publisher の廃止依頼の受付と、Curator の見送り。所有チームの検査は申請と同じ規則（設計書「ロールと所有」）。"""

    def __init__(self, store: DraftStore, client: RegistryRecordReader) -> None:
        self.store = store
        self._registry = client

    def _load_management(self, record_id: str) -> Management:
        try:
            management = self.store.get_management(record_id)
        except (BotoCoreError, ClientError):
            raise DeprecationUnavailable() from None
        except ValueError:
            raise DeprecationUnavailable(
                "保存された管理情報の形式が正しくありません。管理者に連絡してください。"
            ) from None
        if management is None or not management.is_managed:
            raise DeprecationRecordNotFound(
                "ポータルで管理されていないレコードの廃止依頼は扱えません。"
            )
        return management

    def create(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """廃止依頼を保存する。対象の所有チームを所属チームに持つ Publisher だけが依頼できる。"""
        require_publisher(caller)
        _check_record_id(record_id)
        if set(body) != {"reason"}:
            raise RequestValidationError(
                "reason を指定してください。ステータスは API ごとに決まるため指定できません。"
            )
        try:
            reason = parse_request_reason(body["reason"])
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        try:
            record = self._registry.get_registry_record(
                registryId=self.store.registry, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DeprecationRecordNotFound() from None
            raise DeprecationUnavailable() from None
        except BotoCoreError:
            raise DeprecationUnavailable() from None
        management = self._load_management(record_id)
        # 対象の所有チームを所属チームに持つ Publisher だけが依頼できる。所属チームのない
        # Publisher は所有チームに含まれないため 403 になる（設計書「ロールと所有」）
        if not management.is_owned_by(caller.teams):
            raise ForbiddenError()
        status = str(record["status"])
        if status == DEPRECATED_STATUS:
            raise DeprecationConflict("このレコードはすでに廃止されています。")
        if status in IN_FLIGHT_STATUSES:
            raise DeprecationConflict(
                "Registry が処理中のため依頼できません。状態が確定してから再度お試しください。"
            )
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
        try:
            self.store.save_deprecation_request(request, audit)
        except StoreConflict as conflict:
            if _LOCK_ITEM in conflict.failed:
                raise DeprecationConflict(_BUSY) from None
            raise DeprecationConflict(
                "このレコードには未解決の廃止依頼があります。Curator の対応をお待ちください。"
            ) from None
        except (BotoCoreError, ClientError):
            raise DeprecationUnavailable() from None
        return 200, request.view()

    def decline(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """未解決の廃止依頼を、理由を付けて見送る。レコードの状態は変えない（公開停止中ならそのまま）。

        画面で確認した依頼（requestId）と現在の依頼が一致するときだけ見送る。
        """
        require_curator(caller)
        _check_record_id(record_id)
        if set(body) != {"requestId", "reason"} or not isinstance(body["requestId"], str):
            raise RequestValidationError("requestId と reason を指定してください。")
        try:
            reason = parse_decline_reason(body["reason"])
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        management = self._load_management(record_id)
        try:
            request = self.store.get_deprecation_request(record_id)
        except (BotoCoreError, ClientError):
            raise DeprecationUnavailable() from None
        except ValueError:
            raise DeprecationUnavailable(
                "保存された廃止依頼の形式が正しくありません。管理者に連絡してください。"
            ) from None
        if request is None or not request.is_open or request.request_id != body["requestId"]:
            raise DeprecationConflict(
                "見送る廃止依頼が見つかりません。すでに対応済みか、新しい依頼に置き換わっています。"
            )
        principal = principal_of(caller)
        at = now()
        declined = request.resolve(RESOLUTION_DECLINED, reason, principal, at)
        audit = _audit(DECLINE_DEPRECATION_KIND, principal, management, record_id, reason, at)
        try:
            self.store.decline_deprecation_request(declined, audit)
        except StoreConflict as conflict:
            if _LOCK_ITEM in conflict.failed:
                raise DeprecationConflict(_BUSY) from None
            raise DeprecationConflict(
                "廃止依頼の状態が変わりました。最新の状態を確認してください。"
            ) from None
        except (BotoCoreError, ClientError):
            raise DeprecationUnavailable() from None
        return 200, declined.view()
