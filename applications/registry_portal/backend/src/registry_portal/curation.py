"""審査待ちの一覧と、Curator の決裁（公開承認・却下）。

決裁は決裁 handler だけで行う。クライアントから AWS の status は受け取らず、
API ごと（approve / reject）に遷移先を決める。手順は設計書「内容を確認してから決裁する」に従う。

1. レコード単位のロックを取り、決裁の意図を記録する
2. 現物と申請スナップショットを照合する（状態・スナップショットのハッシュ・内容ハッシュ・自己承認）
3. AWS の UpdateRegistryRecordStatus を呼ぶ
4. 再取得して確認してから成功を記録する。AWS が失敗したら承認済みと表示しない
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.curation import (
    DecisionInput,
    SelfApprovalError,
    check_decidable,
    check_not_self_approval,
    published_item,
)
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    IN_FLIGHT_STATUSES,
    OperationState,
    OperationStatus,
    PENDING_APPROVAL_STATUS,
    PUBLISHED_STATUS,
    RECORD_ID_PATTERN,
    REJECTED_STATUS,
    REJECT_KIND,
    DraftOperation,
    PrincipalRecord,
    Management,
    content_hash,
    digest,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.services.draft_store import DraftStore, StoreConflict

# AWS が入力や設定を理由に処理しなかったと確定できるエラー（drafts.py と同じ分類）
AWS_REJECTED_ERRORS = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "ServiceQuotaExceededException",
    }
)
AWS_THROTTLED_ERRORS = frozenset({"ThrottlingException"})
# 決裁の本文の上限。スナップショットのハッシュ（64文字）と決裁理由（255文字）に余裕を持たせる
DECISION_BODY_MAX_LENGTH = 2048
DecisionResponse = tuple[int, Any]


class RegistryStatusClient(Protocol):
    """審査と決裁が使う Registry 管理 API（agent-registry-control）の操作。"""

    def get_registry_record(self, *, registryId: str, recordId: str) -> Mapping[str, Any]: ...

    def list_registry_records(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def update_registry_record_status(self, **kwargs: Any) -> Mapping[str, Any]: ...


class DecisionConflict(ApiError):
    """審査対象が変わったか、決裁できる状態ではない。再読み込みして確認する。"""

    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "審査対象が変更されたか、別の操作が処理中です。最新の内容を確認してください。"


class DecisionUnavailable(ApiError):
    status = 502
    code = "REGISTRY_UNAVAILABLE"
    default_message = (
        "Registry または補助情報を取得できませんでした。時間をおいて再度お試しください。"
    )


class DecisionThrottled(ApiError):
    """AWS が混雑で決裁を処理しなかった。書き込みは行われていないため、失敗として確定する。"""

    status = 429
    code = "REGISTRY_THROTTLED"
    default_message = (
        "Registry が混み合っているため決裁できませんでした。時間をおいて再度実行してください。"
    )


class ReviewNotFound(ApiError):
    status = 404
    code = "REVIEW_NOT_FOUND"
    default_message = "審査対象が見つかりません。一覧から確認してください。"


def require_curator(caller: Caller) -> None:
    """審査と決裁は Curator の業務ロールだけが行える（設計書「ロールと所有」）。"""
    if Role.CURATOR not in caller.roles:
        raise ForbiddenError("審査と決裁は Curator のみ行えます。")


def check_idempotency_key(key: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
        raise RequestValidationError(
            "Idempotency-Key は1〜128文字の英数字・_・- で指定してください。"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_request_id(operation: DraftOperation, response: Mapping[str, Any]) -> None:
    request_id = response.get("ResponseMetadata", {}).get("RequestId")
    if isinstance(request_id, str) and request_id:
        operation["awsRequestId"] = request_id


def _status(operation: Mapping[str, Any], state: OperationState, message: str) -> OperationStatus:
    return {
        "operationId": operation["operationId"],
        "operationState": state,
        "message": message,
    }


def _pending(operation: Mapping[str, Any]) -> DecisionResponse:
    return 202, _status(
        operation,
        "VERIFYING",
        "結果確認中です。確認が終わるまで、このレコードへの書き込みを停止しています。",
    )


def _failed(operation: Mapping[str, Any]) -> DecisionResponse:
    return 200, _status(
        operation, "FAILED", "決裁は失敗しました。最新の状態を確認して再度実行してください。"
    )


def _snapshot_fields(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """保存した申請スナップショットから、保存用のキーを除いた項目を返す。"""
    return {key: value for key, value in snapshot.items() if key not in ("pk", "sk")}


class RecordCuration:
    """審査待ちの一覧・詳細と、公開承認・却下の決裁。"""

    def __init__(self, registry: str, store: DraftStore, client: RegistryStatusClient) -> None:
        self.registry = registry
        self.store = store
        self._registry = client

    def list_reviews(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        """審査待ちの一覧。AWS の承認待ちを正本とし、そこに補助情報を結合する。"""
        require_curator(caller)
        if set(query) - {"nextToken"}:
            raise RequestValidationError("一覧には nextToken のみ指定できます。")
        token = query.get("nextToken")
        if token is not None and (
            not token or len(token) > 2048 or any(char.isspace() for char in token)
        ):
            raise RequestValidationError(
                "ページの指定が正しくありません。最初から表示してください。"
            )
        params: dict[str, Any] = {
            "registryId": self.registry,
            "maxResults": 20,
            "filters": [{"name": "status", "values": [PENDING_APPROVAL_STATUS]}],
        }
        if token is not None:
            params["nextToken"] = token
        try:
            response = self._registry.list_registry_records(**params)
            items = [self._summary(record) for record in response["registryRecords"]]
            return 200, {"items": items, "nextToken": response.get("nextToken")}
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None

    def _summary(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """AWS の承認待ちレコード1件に、申請の補助情報を結合する。"""
        record_id = str(record["recordId"])
        management = self._load_management(record_id)
        lock = self._get_lock(record_id)
        item: dict[str, Any] = {
            "recordId": record_id,
            "name": record["name"],
            "displayName": record.get("displayName", record["name"]),
            "recordType": record["recordType"],
            "recordVersion": record.get("recordVersion", ""),
            "status": record["status"],
            "managed": management is not None and management.item.get("managed") is True,
            "ownerTeam": management.owner_team if management is not None else None,
            "submissionId": None,
            "snapshotHash": None,
            "reason": None,
            "submittedAt": None,
            "submittedBy": None,
            "invalidated": False,
            "operationId": lock["operationId"] if lock else None,
        }
        if management is None or not item["managed"]:
            return item
        submission_id = management.item.get("activeSubmissionId")
        if not isinstance(submission_id, str) or not submission_id:
            return item
        submission = self._get_submission(record_id, submission_id)
        if submission is None:
            return item
        item.update(
            submissionId=submission_id,
            snapshotHash=submission.get("snapshotHash"),
            reason=submission.get("reason"),
            submittedAt=submission.get("submittedAt"),
            submittedBy=submission.get("submittedBy"),
            invalidated=bool(submission.get("invalidated")),
        )
        return item

    def review(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        """審査対象の詳細。申請のスナップショットと、差分の比較対象（公開中の版）を返す。"""
        require_curator(caller)
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            record = self._registry.get_registry_record(
                registryId=self.registry, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ReviewNotFound() from None
            raise DecisionUnavailable() from None
        except BotoCoreError:
            raise DecisionUnavailable() from None
        if record["status"] != PENDING_APPROVAL_STATUS:
            raise ReviewNotFound()
        management = self._load_management(record_id)
        submission_id = management.item.get("activeSubmissionId") if management else None
        submission = (
            self._get_submission(record_id, str(submission_id))
            if isinstance(submission_id, str) and submission_id
            else None
        )
        return 200, {
            "recordId": record_id,
            "name": record["name"],
            "displayName": record.get("displayName", record["name"]),
            "description": record.get("description", ""),
            "recordType": record["recordType"],
            "recordVersion": record.get("recordVersion", ""),
            "ownerTeam": management.owner_team if management else None,
            "status": record["status"],
            "managed": management is not None and management.item.get("managed") is True,
            "publishedVersion": management.published_version(record) if management else None,
            "submissionId": submission_id if submission is not None else None,
            "snapshotHash": submission.get("snapshotHash") if submission else None,
            "invalidated": bool(submission.get("invalidated")) if submission else False,
            "snapshot": _snapshot_fields(submission) if submission else None,
            "published": self._published_snapshot(record_id, management, record),
        }

    def _published_snapshot(
        self, record_id: str, management: Management | None, record: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """差分の比較対象として、公開中の版のスナップショットを返す。"""
        if management is None:
            return None
        published_version = management.published_version(record)
        if published_version is None:
            return None
        try:
            stored = self.store.get(
                self.store.record_pk(record_id), f"PUBLISHED#{published_version}"
            )
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None
        if stored is None:
            return None
        return {
            "recordVersion": published_version,
            "contact": stored.get("contact"),
            "termsOfUse": stored.get("termsOfUse"),
            "connectionReference": stored.get("connectionReference"),
            "relatedReferences": stored.get("relatedReferences", []),
        }

    def decide(
        self,
        caller: Caller,
        record_id: str,
        body: Mapping[str, Any],
        key: str,
        *,
        approved: bool,
    ) -> DecisionResponse:
        """公開承認（approved=True）または却下。AWS の status はクライアントから受け取らない。"""
        require_curator(caller)
        check_idempotency_key(key)
        kind = APPROVE_KIND if approved else REJECT_KIND
        try:
            request = DecisionInput.parse(body, reason_required=not approved)
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        management = self._load_management(record_id)
        if management is None or management.item.get("managed") is not True:
            raise DecisionConflict(
                "ポータルで管理されていないレコードは決裁できません。管理者に連絡してください。"
            )
        principal: PrincipalRecord = {
            "issuer": caller.principal.issuer,
            "subject": caller.principal.subject,
        }
        operation: DraftOperation = {
            "operationId": digest([kind, self.registry, record_id, principal, key]),
            "ownerTeam": management.owner_team,
            "principal": principal,
            "kind": kind,
            "draft": {},
            "state": "IN_PROGRESS",
            "recordId": record_id,
            "lockPk": self.store.record_pk(record_id),
            "startedAt": _now(),
            "management": dict(management.item),
            "reason": request.reason,
            "snapshotHash": request.snapshot_hash,
            "requestHash": digest(body),
        }
        try:
            existing = self.store.get_operation(operation["operationId"])
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None
        if existing:
            return self._replay(existing, operation["requestHash"])
        try:
            self.store.begin(operation)
        except StoreConflict:
            raise DecisionConflict() from None
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None
        try:
            try:
                self._check_and_pin(operation)
            except ApiError as error:
                # AWS へ書き込む前に止めた操作は失敗に確定し、ロックを解除する
                self._abort_quietly(operation, error.code)
                raise
            except (BotoCoreError, ClientError):
                self._abort_quietly(operation, DecisionUnavailable.code)
                raise DecisionUnavailable() from None
            # 決裁の対象を固定してから AWS を呼ぶ。意図と対象の記録が先、AWS の呼び出しが後
            self.store.save_pending(operation)
            return self._call_status_api(operation)
        except (BotoCoreError, ClientError, StoreConflict):
            return _pending(operation)

    def _replay(self, existing: DraftOperation, request_hash: str) -> DecisionResponse:
        """同じ Idempotency-Key の決裁を再送したとき、その操作の結果を返す。"""
        if existing.get("requestHash") != request_hash:
            raise DecisionConflict("同じ Idempotency-Key に異なる内容は指定できません。")
        if "expectedContentHash" not in existing:
            # 意図の記録後に検査の途中で止まった操作。検査からやり直す
            try:
                self._check_and_pin(existing)
            except ApiError as error:
                self._abort_quietly(existing, error.code)
                raise
            except (BotoCoreError, ClientError):
                return _pending(existing)
        return self.settle(existing, allow_reissue=True)

    def _check_and_pin(self, operation: DraftOperation) -> Mapping[str, Any]:
        """現物と申請スナップショットを照合し、決裁の対象を操作に固定する。

        Curator が見たスナップショットのハッシュと、現物から計算し直した内容ハッシュの
        両方が申請時と一致しなければ、決裁しない（設計書「内容を確認してから決裁する」）。
        """
        record_id = operation["recordId"]
        try:
            record = self._registry.get_registry_record(
                registryId=self.registry, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ReviewNotFound() from None
            raise
        try:
            check_decidable(str(record["status"]))
        except ValueError as error:
            raise DecisionConflict(str(error)) from None
        management = Management(operation["management"])
        submission_id = management.item.get("activeSubmissionId")
        if not isinstance(submission_id, str) or not submission_id:
            raise DecisionConflict(
                "ポータルの申請が確認できないため決裁できません。Publisher に再申請を依頼してください。"
            )
        submission = self._get_submission(record_id, submission_id)
        if submission is None:
            raise DecisionConflict(
                "ポータルの申請が確認できないため決裁できません。Publisher に再申請を依頼してください。"
            )
        if submission.get("invalidated"):
            raise DecisionConflict(
                "この申請は審査中の編集で無効になっています。最新の申請を確認してください。"
            )
        if submission.get("snapshotHash") != operation["snapshotHash"]:
            raise DecisionConflict(
                "審査したスナップショットが現在の申請と一致しません。"
                "最新の申請を読み込んでから決裁してください。"
            )
        if content_hash(record, management) != submission.get("contentHash"):
            raise DecisionConflict(
                "申請時からレコードの内容が変わっています。最新の申請を確認してください。"
            )
        try:
            check_not_self_approval(
                operation["principal"],
                submission.get("submittedBy"),
                submission.get("lastEditedBy"),
            )
        except SelfApprovalError:
            raise ForbiddenError(
                "申請者と最終編集者は決裁できません。別の Curator が審査してください。"
            ) from None
        operation["submissionId"] = submission_id
        operation["expectedContentHash"] = str(submission["contentHash"])
        operation["recordVersion"] = str(submission["record"]["recordVersion"])
        return record

    def _call_status_api(self, operation: DraftOperation) -> DecisionResponse:
        """AWS の状態更新 API を呼び、結果を照合する。"""
        target = PUBLISHED_STATUS if operation["kind"] == APPROVE_KIND else REJECTED_STATUS
        try:
            response = self._registry.update_registry_record_status(
                registryId=self.registry,
                recordId=operation["recordId"],
                status=target,
                statusReason=operation["reason"],
            )
        except ClientError as error:
            return self._aws_error(operation, error)
        _record_request_id(operation, response)
        operation["decisionAccepted"] = True
        return self.settle(operation, allow_reissue=True)

    def _aws_error(self, operation: DraftOperation, error: ClientError) -> DecisionResponse:
        """AWS が処理せず拒否したと確定できる失敗は操作を失敗にし、それ以外は結果確認中にする。"""
        code = error.response["Error"]["Code"]
        _record_request_id(operation, error.response)
        if code in AWS_THROTTLED_ERRORS:
            self.store.abort(operation, "AWS_THROTTLED")
            raise DecisionThrottled() from None
        if code == "ConflictException":
            self.store.abort(operation, "AWS_CONFLICT")
            raise DecisionConflict(
                "レコードの状態が変わったため決裁できませんでした。最新の状態を確認してください。"
            ) from None
        if code in AWS_REJECTED_ERRORS:
            self.store.abort(operation, "AWS_REJECTED")
            raise RequestValidationError(
                "Registry が決裁を拒否しました。入力と設定を確認してください。"
            ) from None
        return _pending(operation)

    def settle(self, operation: DraftOperation, *, allow_reissue: bool) -> DecisionResponse:
        """操作の結果を確定する。確定できなければ結果確認中を返す。"""
        if operation["state"] == "SUCCEEDED":
            return 200, operation["result"]
        if operation["state"] == "FAILED":
            return _failed(operation)
        try:
            return self._settle(operation, allow_reissue=allow_reissue)
        except (BotoCoreError, ClientError, StoreConflict):
            return _pending(operation)

    def _settle(self, operation: DraftOperation, *, allow_reissue: bool) -> DecisionResponse:
        """AWS のレコードを読んで決裁の結果を確定する。

        allow_reissue は、意図の記録後に AWS の呼び出し（または受け付けの確認）まで至らなかった
        操作に同じ決裁を送り直せるかを示す。決裁 handler だけが true にする。
        先の呼び出しが AWS に届いていた可能性があるため、送り直しのエラーでは失敗と確定しない。
        """
        if "expectedContentHash" not in operation:
            return _pending(operation)
        record = self._registry.get_registry_record(
            registryId=self.registry, recordId=operation["recordId"]
        )
        status = str(record["status"])
        target = PUBLISHED_STATUS if operation["kind"] == APPROVE_KIND else REJECTED_STATUS
        if status == PENDING_APPROVAL_STATUS and not operation.get("decisionAccepted"):
            if not allow_reissue:
                # 管理 handler の操作照会は決裁の権限を持たないため、AWS を呼び直さない
                return _pending(operation)
            try:
                response = self._registry.update_registry_record_status(
                    registryId=self.registry,
                    recordId=operation["recordId"],
                    status=target,
                    statusReason=operation["reason"],
                )
                _record_request_id(operation, response)
                operation["decisionAccepted"] = True
            except ClientError as error:
                _record_request_id(operation, error.response)
            self.store.save_pending(operation)
            record = self._registry.get_registry_record(
                registryId=self.registry, recordId=operation["recordId"]
            )
            status = str(record["status"])
        if status in IN_FLIGHT_STATUSES:
            return _pending(operation)
        if status == PENDING_APPROVAL_STATUS:
            # AWS の受け付けを確認した後も承認待ちが見えるときは、反映を待っている
            return _pending(operation)
        management = Management(operation["management"])
        if (
            status != target
            or str(record.get("recordVersion")) != operation["recordVersion"]
            or content_hash(record, management) != operation["expectedContentHash"]
        ):
            # 決裁が反映されなかったか、別の内容が確定している。反映先の状態を失敗のコードに残す
            self.store.abort(operation, "CONTENT_CHANGED" if status == target else status)
            return _failed(operation)
        return self._finish(operation, record, management)

    def _finish(
        self, operation: DraftOperation, record: Mapping[str, Any], management: Management
    ) -> DecisionResponse:
        """決裁の成功を確定する。公開承認では、Curator が確認した内容を公開版として保存する。"""
        approved = operation["kind"] == APPROVE_KIND
        published: Mapping[str, Any] | None = None
        management_item = {**management.item, "activeSubmissionId": None}
        if approved:
            submission = self._get_submission(operation["recordId"], str(operation["submissionId"]))
            if submission is None:
                raise DecisionUnavailable("申請スナップショットを読み取れません。")
            published = published_item(submission)
            management_item["publishedVersion"] = operation["recordVersion"]
        after = Management(management_item)
        result: dict[str, Any] = {
            "recordId": operation["recordId"],
            "name": record["name"],
            "displayName": record.get("displayName", record["name"]),
            "recordType": record["recordType"],
            "recordVersion": record["recordVersion"],
            "status": record["status"],
            "ownerTeam": operation["ownerTeam"],
            "publishedVersion": (
                operation["recordVersion"] if approved else after.published_version(record)
            ),
            "operationId": operation["operationId"],
            "operationState": "SUCCEEDED",
            "submissionId": operation["submissionId"],
            "snapshotHash": operation["snapshotHash"],
        }
        self.store.finish_decision(operation, result, after, published, operation["recordVersion"])
        return 200, result

    def _abort_quietly(self, operation: DraftOperation, code: str) -> None:
        try:
            self.store.abort(operation, code)
        except (BotoCoreError, ClientError, StoreConflict):
            # 失敗を確定できなければロックは残り、結果確認中として扱われる
            pass

    def _load_management(self, record_id: str) -> Management | None:
        try:
            return self.store.get_management(record_id)
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None
        except ValueError:
            raise DecisionUnavailable(
                "保存された管理情報の形式が正しくありません。管理者に連絡してください。"
            ) from None

    def _get_submission(self, record_id: str, submission_id: str) -> dict[str, Any] | None:
        try:
            return self.store.get_submission(record_id, submission_id)
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None

    def _get_lock(self, record_id: str) -> dict[str, Any] | None:
        try:
            return self.store.get_lock(record_id)
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None


def settle_decision(
    curation: RecordCuration,
    operation: DraftOperation,
    *,
    caller: Caller,
) -> DecisionResponse:
    """操作照会（GET /api/operations）から決裁操作を確定する。

    決裁操作は Curator だけが参照できる。管理 handler から呼ぶため、AWS を呼び直さない
    （管理 handler の実行ロールには決裁の権限がないため）。同じキーでの再送は決裁 handler が担う。
    """
    require_curator(caller)
    return curation.settle(operation, allow_reissue=False)
