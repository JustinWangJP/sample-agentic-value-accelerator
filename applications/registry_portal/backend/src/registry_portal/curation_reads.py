"""審査と公開管理の操作が共通で使う、読み取りと例外と認可。

使うのは、審査待ちの一覧・審査・決裁・公開停止・廃止・公開管理の照会・廃止依頼の受付と見送り。
Registry と補助ストアの読み取りの失敗は、空の結果にせず 502（RegistryUnavailable）として区別する
（CODING_STANDARDS.md「通信や保存内容の異常を空の結果に変換しない」）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeGuard, cast

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.curation import (
    ATTACHMENT_AVAILABLE,
    ATTACHMENT_MISSING,
    ATTACHMENT_NONE,
    AttachmentState,
    RecordState,
    published_base,
)
from registry_portal.domain.deprecation import DeprecationRequest
from registry_portal.domain.drafts import PUBLISHED_STATUS, Management, RecordStatus
from registry_portal.domain.submissions import snapshot_fields
from registry_portal.errors import ApiError, ForbiddenError
from registry_portal.services.supplement_store import SupplementStore

# 別の操作がレコードのロックを持っているときの理由。公開管理の照会・廃止依頼の受付と見送りで共通
RECORD_BUSY = "別の操作が処理中です。結果が確定してから操作してください。"
_STORED_MANAGEMENT_INVALID = (
    "保存された管理情報の形式が正しくありません。管理者に連絡してください。"
)
_STORED_REQUEST_INVALID = "保存された廃止依頼の形式が正しくありません。管理者に連絡してください。"


class RegistryReadClient(Protocol):
    """審査と公開管理の読み取りが使う Registry 管理 API（agent-registry-control）の操作。"""

    def get_registry_record(self, *, registryId: str, recordId: str) -> Mapping[str, Any]: ...

    def list_registry_records(self, **kwargs: Any) -> Mapping[str, Any]: ...


class RegistryStatusClient(RegistryReadClient, Protocol):
    """決裁・公開停止・廃止が使う Registry 管理 API。読み取りに加えて状態を更新する。"""

    def update_registry_record_status(self, **kwargs: Any) -> Mapping[str, Any]: ...


class RegistryUnavailable(ApiError):
    """Registry または補助ストアを読めなかった。0件や「なし」とは区別する。"""

    status = 502
    code = "REGISTRY_UNAVAILABLE"
    default_message = (
        "Registry または補助情報を取得できませんでした。時間をおいて再度お試しください。"
    )


class PendingApprovalNotFound(ApiError):
    """審査待ち（AWS の PENDING_APPROVAL）の Registry レコードが見つからない。"""

    status = 404
    code = "PENDING_APPROVAL_NOT_FOUND"
    default_message = "審査待ちの申請が見つかりません。一覧から確認してください。"


class RecordNotFound(ApiError):
    """公開停止・廃止・公開管理・廃止依頼の対象の Registry レコードが見つからない（削除済みを含む）。"""

    status = 404
    code = "RECORD_NOT_FOUND"
    default_message = (
        "指定された Registry レコードは見つかりません。レコード ID を確認してください。"
    )


class RegistryRecordMissing(Exception):
    """Registry にレコードがない（404）。呼び出し側が操作に合った API エラーに変換する。"""


def require_curator(caller: Caller) -> None:
    """審査・決裁と公開管理は Curator の業務ロールだけが行える（設計書「ロールと所有」）。"""
    if Role.CURATOR not in caller.roles:
        raise ForbiddenError("審査・決裁と公開管理は Curator のみ行えます。")


def display_name(record: Mapping[str, Any]) -> str:
    """表示名。AWS が displayName を返さないレコードは名前で代用する。"""
    return str(record.get("displayName", record["name"]))


def is_managed(management: Management | None) -> TypeGuard[Management]:
    """ポータルの管理下にあるか。未管理（取り込み前）のレコードは決裁できない。"""
    return management is not None and management.is_managed


def is_suspended(management: Management | None, record: Mapping[str, Any]) -> bool:
    """公開停止中か。管理情報がなければ公開停止もしていない。

    AWS が公開中（APPROVED）を返すなら、公開停止の印が残っていても公開停止中ではない
    （ポータルの外で再承認された場合。印はポータルの公開承認でしか消えない）。
    """
    return (
        management is not None and management.is_suspended and record["status"] != PUBLISHED_STATUS
    )


def record_state(record: Mapping[str, Any], management: Management | None) -> RecordState:
    """決裁・公開停止・廃止・廃止依頼の前提の検査に使う、レコードの現在の状態。"""
    return RecordState(
        status=cast(RecordStatus, str(record["status"])),
        published_version=management.published_version(record) if management else None,
    )


class CurationReads:
    """Curator の操作が使う Registry と補助ストアの読み取り。"""

    def __init__(self, registry: str, store: SupplementStore, client: RegistryReadClient) -> None:
        self.registry = registry
        self.store = store
        self._client = client

    def get_record(self, record_id: str) -> Mapping[str, Any]:
        """AWS のレコードを読む。存在しなければ RegistryRecordMissing、それ以外の AWS のエラーは呼び出し側に任せる。"""
        try:
            return self._client.get_registry_record(registryId=self.registry, recordId=record_id)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                raise RegistryRecordMissing() from None
            raise

    def require_record(self, record_id: str) -> Mapping[str, Any]:
        """AWS のレコードを読む。存在しなければ 404（RecordNotFound）、読めなければ 502。"""
        try:
            return self.get_record(record_id)
        except RegistryRecordMissing:
            raise RecordNotFound() from None
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None

    def list_records(self, **params: Any) -> Mapping[str, Any]:
        """AWS の ListRegistryRecords。エラーは呼び出し側に任せる。"""
        return self._client.list_registry_records(registryId=self.registry, **params)

    def load_management(self, record_id: str) -> Management | None:
        try:
            return self.store.get_management(record_id)
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        except ValueError:
            raise RegistryUnavailable(_STORED_MANAGEMENT_INVALID) from None

    def get_submission(self, record_id: str, submission_id: str) -> dict[str, Any] | None:
        try:
            return self.store.get_submission(record_id, submission_id)
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None

    def locking_operation_id(self, record_id: str) -> str | None:
        """レコードのロックを持つ処理中の操作の ID。ロックがなければ None。"""
        try:
            lock = self.store.get_lock(record_id)
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        return str(lock["operationId"]) if lock else None

    def deprecation_request(self, record_id: str) -> DeprecationRequest | None:
        """レコードの廃止依頼（解決済みを含む）。保存内容の形式の異常は 502。"""
        try:
            return self.store.get_deprecation_request(record_id)
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        except ValueError:
            raise RegistryUnavailable(_STORED_REQUEST_INVALID) from None

    def open_deprecation_request(self, record_id: str) -> DeprecationRequest | None:
        """未解決の廃止依頼。なければ None。"""
        request = self.deprecation_request(record_id)
        return request if request is not None and request.is_open else None

    def open_deprecation_requests(self) -> list[DeprecationRequest]:
        """未解決の廃止依頼を、古い順（滞留している順）に返す。保存内容の形式の異常は 502。"""
        try:
            requests = self.store.list_deprecation_requests()
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        except ValueError:
            raise RegistryUnavailable(_STORED_REQUEST_INVALID) from None
        return sorted(
            (request for request in requests if request.is_open),
            key=lambda request: request.requested_at,
        )

    def active_submission(
        self, record_id: str, management: Management | None
    ) -> tuple[AttachmentState, str | None, dict[str, Any] | None]:
        """管理情報が指す現在の申請。指しているのに読めなければ MISSING とし、「申請なし」と区別する。"""
        if not is_managed(management):
            return ATTACHMENT_NONE, None, None
        submission_id = management.item.get("activeSubmissionId")
        if not isinstance(submission_id, str) or not submission_id:
            return ATTACHMENT_NONE, None, None
        submission = self.get_submission(record_id, submission_id)
        if submission is None:
            return ATTACHMENT_MISSING, submission_id, None
        return ATTACHMENT_AVAILABLE, submission_id, submission

    def published_snapshot(
        self, record_id: str, management: Management | None, record: Mapping[str, Any]
    ) -> tuple[AttachmentState, dict[str, Any] | None]:
        """差分の比較対象として、公開中の版のスナップショットを返す。

        公開中の版があるのにスナップショットが読めなければ MISSING とし、「初回公開」と区別する（ADR-0007）。
        """
        published_version = management.published_version(record) if management else None
        if published_version is None:
            return ATTACHMENT_NONE, None
        try:
            stored = self.store.get_published(record_id, published_version)
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        if stored is None:
            return ATTACHMENT_MISSING, None
        return ATTACHMENT_AVAILABLE, published_base(snapshot_fields(stored), published_version)


def record_summary(record: Mapping[str, Any], management: Management | None) -> dict[str, Any]:
    """一覧・審査・公開管理に並べる、レコードの要約（種別によらず共通する項目）。"""
    return {
        "recordId": record["recordId"],
        "name": record["name"],
        "displayName": display_name(record),
        "recordType": record["recordType"],
        "recordVersion": record.get("recordVersion", ""),
        "status": record["status"],
        "managed": is_managed(management),
        "ownerTeam": management.owner_team if management is not None else None,
    }
