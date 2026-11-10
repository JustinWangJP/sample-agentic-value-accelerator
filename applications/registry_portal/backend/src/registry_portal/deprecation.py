"""Publisher の廃止依頼（POST /api/records/{recordId}/deprecation-requests）。

廃止依頼は AWS の状態を変えない。補助テーブルに未解決の依頼として保存し、審査待ちの一覧
（GET /api/reviews）で申請と並んで Curator に表示する。Curator が公開停止・廃止（決裁 handler）で
依頼を解決済みにする（設計書「業務上の状態」「ロールと所有」）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.domain.drafts import (
    DEPRECATED_STATUS,
    IN_FLIGHT_STATUSES,
    RECORD_ID_PATTERN,
    digest,
    is_principal,
)
from registry_portal.domain.secrets import scan_text
from registry_portal.drafts import require_publisher
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import now, principal_of
from registry_portal.services.draft_store import (
    DEPRECATION_OPEN,
    DraftStore,
    StoreConflict,
)

# 依頼理由の上限。AWS には送らずポータルの記録にだけ残す
DEPRECATION_REASON_MAX_LENGTH = 255
# 依頼の本文の上限。理由（255文字）に余裕を持たせる
DEPRECATION_BODY_MAX_LENGTH = 1024


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
    """すでに未解決の廃止依頼がある、またはレコードの状態が依頼を受け付けない。"""

    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "レコードの状態が変わったため実行できません。最新の状態を確認してください。"


class RegistryRecordReader(Protocol):
    """廃止依頼が使う Registry 管理 API（agent-registry-control）の読み取り。"""

    def get_registry_record(self, *, registryId: str, recordId: str) -> Mapping[str, Any]: ...


def _parse_body(body: Mapping[str, Any]) -> str:
    if set(body) != {"reason"}:
        raise RequestValidationError(
            "reason を指定してください。ステータスは API ごとに決まるため指定できません。"
        )
    reason = body["reason"]
    stripped = reason.strip() if isinstance(reason, str) else ""
    if not stripped:
        raise RequestValidationError("廃止依頼の理由は必須です。終了の背景を記載してください。")
    if len(stripped) > DEPRECATION_REASON_MAX_LENGTH:
        raise RequestValidationError(
            f"廃止依頼の理由は{DEPRECATION_REASON_MAX_LENGTH}文字以内で指定してください。"
        )
    if scan_text("reason", stripped):
        raise RequestValidationError(
            "廃止依頼の理由に秘密情報らしき値が含まれています。保管先への参照を記載してください。"
        )
    return stripped


class DeprecationRequests:
    """Publisher の廃止依頼の受付。所有チームの検査は申請と同じ規則（設計書「ロールと所有」）。"""

    def __init__(self, store: DraftStore, client: RegistryRecordReader) -> None:
        self.store = store
        self._registry = client

    def create(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """廃止依頼を保存する。対象の所有チームを所属チームに持つ Publisher だけが依頼できる。"""
        require_publisher(caller)
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        reason = _parse_body(body)
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
                "ポータルで管理されていないレコードには廃止を依頼できません。"
            )
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
        request: dict[str, Any] = {
            "state": DEPRECATION_OPEN,
            "requestId": digest(["DEPRECATION_REQUEST", self.store.registry, record_id, now()]),
            "recordId": record_id,
            "reason": reason,
            "requestedBy": principal,
            "requestedAt": now(),
        }
        try:
            self.store.save_deprecation_request(record_id, request)
        except StoreConflict:
            raise DeprecationConflict(
                "このレコードには未解決の廃止依頼があります。Curator の対応をお待ちください。"
            ) from None
        except (BotoCoreError, ClientError):
            raise DeprecationUnavailable() from None
        return 200, {**request, "requestedBy": dict(principal)}


def deprecation_request_view(request: Mapping[str, Any]) -> dict[str, Any]:
    """審査待ちの一覧に並べる廃止依頼の項目。保存用のキーを除く。"""
    requested_by = request["requestedBy"]
    if not is_principal(requested_by):
        raise ValueError("保存された廃止依頼の形式が正しくありません。")
    return {
        "requestId": request.get("requestId"),
        "reason": request["reason"],
        "requestedAt": request["requestedAt"],
        "requestedBy": dict(requested_by),
    }
