"""審査待ちの一覧と、Curator の決裁（公開承認・却下）。

決裁は決裁 handler だけで行う。クライアントから AWS の status は受け取らず、
API ごと（approve / reject）に遷移先を決める。手順は設計書「内容を確認してから決裁する」に従う。

1. レコード単位のロックを取り、決裁の意図を記録する
2. 現物と申請スナップショットを照合する（状態・スナップショットのハッシュ・内容ハッシュ・自己承認）
3. AWS の UpdateRegistryRecordStatus を呼ぶ
4. 再取得して確認してから成功を記録する。AWS が失敗したら承認済みと表示しない
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeGuard

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.curation import (
    ATTACHMENT_AVAILABLE,
    ATTACHMENT_MISSING,
    ATTACHMENT_NONE,
    DECISION_TARGETS,
    AttachmentState,
    DecisionInput,
    SelfApprovalError,
    check_decidable,
    check_not_self_approval,
    published_base,
    published_item,
)
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    IN_FLIGHT_STATUSES,
    PENDING_APPROVAL_STATUS,
    RECORD_ID_PATTERN,
    DecisionOperation,
    Management,
    OperationKind,
    content_hash,
    digest,
)
from registry_portal.domain.submissions import recompute_snapshot_hash, snapshot_fields
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import (
    AWS_REJECTED_ERRORS,
    AWS_THROTTLED_ERRORS,
    check_idempotency_key,
    now,
    operation_status,
    principal_of,
    record_request_id,
)
from registry_portal.services.draft_store import DraftStore, StoreConflict

# 決裁の本文の上限。スナップショットのハッシュ（64文字）と決裁理由（255文字）に余裕を持たせる
DECISION_BODY_MAX_LENGTH = 2048
DecisionResponse = tuple[int, Any]

_SUBMISSION_UNCONFIRMED = (
    "ポータルの申請が確認できないため決裁できません。Publisher に再申請を依頼してください。"
)


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


class PendingApprovalNotFound(ApiError):
    """審査待ち（AWS の PENDING_APPROVAL）の Registry レコードが見つからない。"""

    status = 404
    code = "PENDING_APPROVAL_NOT_FOUND"
    default_message = "審査待ちの申請が見つかりません。一覧から確認してください。"


def require_curator(caller: Caller) -> None:
    """審査と決裁は Curator の業務ロールだけが行える（設計書「ロールと所有」）。"""
    if Role.CURATOR not in caller.roles:
        raise ForbiddenError("審査と決裁は Curator のみ行えます。")


def _pending(operation: Mapping[str, Any]) -> DecisionResponse:
    return 202, operation_status(
        operation,
        "VERIFYING",
        "結果確認中です。確認が終わるまで、このレコードへの書き込みを停止しています。",
    )


def _failed(operation: Mapping[str, Any]) -> DecisionResponse:
    return 200, operation_status(
        operation, "FAILED", "決裁は失敗しました。最新の状態を確認して再度実行してください。"
    )


def _display_name(record: Mapping[str, Any]) -> str:
    """表示名。AWS が displayName を返さないレコードは名前で代用する。"""
    return str(record.get("displayName", record["name"]))


def _is_managed(management: Management | None) -> TypeGuard[Management]:
    """ポータルの管理下にあるか。未管理（取り込み前）のレコードは決裁できない。"""
    return management is not None and management.item.get("managed") is True


def _snapshot_view(submission: Mapping[str, Any]) -> dict[str, Any]:
    """画面に返す申請スナップショット。保存用のキーを除き、ハッシュを添える。"""
    return {**snapshot_fields(submission), "snapshotHash": submission.get("snapshotHash")}


class RecordCuration:
    """審査待ちの一覧・詳細と、公開承認・却下の決裁。"""

    def __init__(self, registry: str, store: DraftStore, client: RegistryStatusClient) -> None:
        self.registry = registry
        self.store = store
        self._registry_client = client

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
            response = self._registry_client.list_registry_records(**params)
            items = [self._summary(record) for record in response["registryRecords"]]
            return 200, {"items": items, "nextToken": response.get("nextToken")}
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None

    def _summary(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """AWS の承認待ちレコード1件に、申請の補助情報を結合する。"""
        record_id = str(record["recordId"])
        management = self._load_management(record_id)
        lock = self._get_lock(record_id)
        state, submission_id, submission = self._active_submission(record_id, management)
        return {
            "recordId": record_id,
            "name": record["name"],
            "displayName": _display_name(record),
            "recordType": record["recordType"],
            "recordVersion": record.get("recordVersion", ""),
            "status": record["status"],
            "managed": _is_managed(management),
            "ownerTeam": management.owner_team if management is not None else None,
            "submissionState": state,
            "submissionId": submission_id,
            "snapshotHash": submission.get("snapshotHash") if submission else None,
            "reason": submission.get("reason") if submission else None,
            "submittedAt": submission.get("submittedAt") if submission else None,
            "submittedBy": submission.get("submittedBy") if submission else None,
            "invalidated": bool(submission.get("invalidated")) if submission else False,
            "operationId": lock["operationId"] if lock else None,
        }

    def _active_submission(
        self, record_id: str, management: Management | None
    ) -> tuple[AttachmentState, str | None, dict[str, Any] | None]:
        """管理情報が指す現在の申請。指しているのに読めなければ MISSING とし、「申請なし」と区別する。"""
        if not _is_managed(management):
            return ATTACHMENT_NONE, None, None
        submission_id = management.item.get("activeSubmissionId")
        if not isinstance(submission_id, str) or not submission_id:
            return ATTACHMENT_NONE, None, None
        submission = self._get_submission(record_id, submission_id)
        if submission is None:
            return ATTACHMENT_MISSING, submission_id, None
        return ATTACHMENT_AVAILABLE, submission_id, submission

    def review(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        """審査対象の詳細。申請のスナップショットと、差分の比較対象（公開中の版）を返す。"""
        require_curator(caller)
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            record = self._get_record(record_id)
        except (BotoCoreError, ClientError):
            raise DecisionUnavailable() from None
        if record["status"] != PENDING_APPROVAL_STATUS:
            raise PendingApprovalNotFound()
        management = self._load_management(record_id)
        state, submission_id, submission = self._active_submission(record_id, management)
        published_state, published = self._published_snapshot(record_id, management, record)
        return 200, {
            "recordId": record_id,
            "name": record["name"],
            "displayName": _display_name(record),
            "description": record.get("description", ""),
            "recordType": record["recordType"],
            "recordVersion": record.get("recordVersion", ""),
            "ownerTeam": management.owner_team if management else None,
            "status": record["status"],
            "managed": _is_managed(management),
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
        }

    def _published_snapshot(
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
            raise DecisionUnavailable() from None
        if stored is None:
            return ATTACHMENT_MISSING, None
        return ATTACHMENT_AVAILABLE, published_base(snapshot_fields(stored), published_version)

    def decide(
        self,
        caller: Caller,
        record_id: str,
        body: Mapping[str, Any],
        key: str,
        *,
        kind: OperationKind,
    ) -> DecisionResponse:
        """公開承認（APPROVE）または却下（REJECT）。AWS の status はクライアントから受け取らない。"""
        require_curator(caller)
        check_idempotency_key(key)
        try:
            request = DecisionInput.parse(body, kind)
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        management = self._load_management(record_id)
        if not _is_managed(management):
            raise DecisionConflict(
                "ポータルで管理されていないレコードは決裁できません。管理者に連絡してください。"
            )
        principal = principal_of(caller)
        operation: DecisionOperation = {
            "operationId": digest([kind, self.registry, record_id, principal, key]),
            "ownerTeam": management.owner_team,
            "principal": principal,
            "kind": kind,
            "state": "IN_PROGRESS",
            "recordId": record_id,
            "lockPk": self.store.record_pk(record_id),
            "startedAt": now(),
            "management": dict(management.item),
            "reason": request.reason,
            "snapshotHash": request.snapshot_hash,
            "requestHash": digest(body),
        }
        try:
            existing = self.store.get_decision_operation(operation["operationId"])
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

    def _replay(self, existing: DecisionOperation, request_hash: str) -> DecisionResponse:
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

    def _get_record(self, record_id: str) -> Mapping[str, Any]:
        """AWS のレコードを読む。存在しなければ 404、それ以外の AWS のエラーは呼び出し側に任せる。"""
        try:
            return self._registry_client.get_registry_record(
                registryId=self.registry, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                raise PendingApprovalNotFound() from None
            raise

    def _check_and_pin(self, operation: DecisionOperation) -> Mapping[str, Any]:
        """現物と申請スナップショットを照合し、決裁の対象を操作に固定する。

        Curator が見たスナップショットのハッシュが、保存済みスナップショットから計算し直したハッシュと
        一致し、現物から計算し直した内容ハッシュも申請時と一致しなければ、決裁しない
        （設計書「内容を確認してから決裁する」）。
        """
        record_id = operation["recordId"]
        record = self._get_record(record_id)
        try:
            check_decidable(str(record["status"]))
        except ValueError as error:
            raise DecisionConflict(str(error)) from None
        management = Management(operation["management"])
        state, submission_id, submission = self._active_submission(record_id, management)
        if state != ATTACHMENT_AVAILABLE or submission is None or submission_id is None:
            raise DecisionConflict(_SUBMISSION_UNCONFIRMED)
        if submission.get("invalidated"):
            raise DecisionConflict(
                "この申請は審査中の編集で無効になっています。最新の申請を確認してください。"
            )
        if recompute_snapshot_hash(submission) != submission.get("snapshotHash"):
            raise DecisionConflict(
                "保存された申請スナップショットが申請時の内容と一致しません。"
                "Publisher に再申請を依頼し、管理者に連絡してください。"
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

    def _send_decision(self, operation: DecisionOperation) -> None:
        """意図に記録した決裁（種別・理由）を AWS の状態更新 API に送る。受け付けられたら記録する。"""
        try:
            response = self._registry_client.update_registry_record_status(
                registryId=self.registry,
                recordId=operation["recordId"],
                status=DECISION_TARGETS[operation["kind"]],
                statusReason=operation["reason"],
            )
        except ClientError as error:
            record_request_id(operation, error.response)
            raise
        record_request_id(operation, response)
        operation["decisionAccepted"] = True

    def _call_status_api(self, operation: DecisionOperation) -> DecisionResponse:
        """AWS の状態更新 API を呼び、結果を照合する。"""
        try:
            self._send_decision(operation)
        except ClientError as error:
            return self._aws_error(operation, error)
        return self.settle(operation, allow_reissue=True)

    def _aws_error(self, operation: DecisionOperation, error: ClientError) -> DecisionResponse:
        """AWS が処理せず拒否したと確定できる失敗は操作を失敗にし、それ以外は結果確認中にする。"""
        code = error.response["Error"]["Code"]
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

    def settle(self, operation: DecisionOperation, *, allow_reissue: bool) -> DecisionResponse:
        """操作の結果を確定する。確定できなければ結果確認中を返す。"""
        if operation["state"] == "SUCCEEDED":
            return 200, operation["result"]
        if operation["state"] == "FAILED":
            return _failed(operation)
        try:
            return self._settle(operation, allow_reissue=allow_reissue)
        except (BotoCoreError, ClientError, StoreConflict):
            return _pending(operation)

    def operation_status(self, caller: Caller, operation: DecisionOperation) -> DecisionResponse:
        """操作照会（GET /api/operations）から決裁操作を確定する。

        決裁操作は、その決裁を行った Curator 本人だけが参照できる（下書きの操作が所有チームに限られるのと同じく、
        操作の主体に限る）。管理 handler から呼ぶため AWS を呼び直さない
        （管理 handler の実行ロールには決裁の権限がないため）。同じキーでの再送は決裁 handler が担う。
        """
        require_curator(caller)
        if operation["principal"] != principal_of(caller):
            raise ForbiddenError("決裁の操作は、決裁した Curator だけが参照できます。")
        return self.settle(operation, allow_reissue=False)

    def _settle(self, operation: DecisionOperation, *, allow_reissue: bool) -> DecisionResponse:
        """AWS のレコードを読んで決裁の結果を確定する。

        allow_reissue は、意図の記録後に AWS の呼び出し（または受け付けの確認）まで至らなかった
        操作に同じ決裁を送り直せるかを示す。決裁 handler だけが true にする。
        先の呼び出しが AWS に届いていた可能性があるため、送り直しのエラーでは失敗と確定しない。
        """
        if "expectedContentHash" not in operation:
            return _pending(operation)
        record = self._get_record(operation["recordId"])
        status = str(record["status"])
        if status == PENDING_APPROVAL_STATUS and not operation.get("decisionAccepted"):
            if not allow_reissue:
                # 管理 handler の操作照会は決裁の権限を持たないため、AWS を呼び直さない
                return _pending(operation)
            try:
                self._send_decision(operation)
            except ClientError:
                # 先の呼び出しが届いていた可能性があるため、失敗とは確定しない
                pass
            self.store.save_pending(operation)
            record = self._get_record(operation["recordId"])
            status = str(record["status"])
        if status in IN_FLIGHT_STATUSES:
            return _pending(operation)
        if status == PENDING_APPROVAL_STATUS:
            # AWS の受け付けを確認した後も承認待ちが見えるときは、反映を待っている
            return _pending(operation)
        target = DECISION_TARGETS[operation["kind"]]
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
        self, operation: DecisionOperation, record: Mapping[str, Any], management: Management
    ) -> DecisionResponse:
        """決裁の成功を確定する。公開承認では、Curator が確認した内容を公開版として保存する。"""
        published: Mapping[str, Any] | None = None
        management_item = {**management.item, "activeSubmissionId": None}
        if operation["kind"] == APPROVE_KIND:
            submission = self._get_submission(operation["recordId"], str(operation["submissionId"]))
            if submission is None:
                raise DecisionUnavailable("申請スナップショットを読み取れません。")
            published = published_item(submission, operation["principal"], now())
            management_item["publishedVersion"] = operation["recordVersion"]
        after = Management(management_item)
        result: dict[str, Any] = {
            "recordId": operation["recordId"],
            "name": record["name"],
            "displayName": _display_name(record),
            "recordType": record["recordType"],
            "recordVersion": record["recordVersion"],
            "status": record["status"],
            "ownerTeam": operation["ownerTeam"],
            "publishedVersion": after.published_version(record),
            "operationId": operation["operationId"],
            "operationState": "SUCCEEDED",
            "submissionId": operation["submissionId"],
            "snapshotHash": operation["snapshotHash"],
        }
        self.store.finish_decision(operation, result, after, published)
        return 200, result

    def _abort_quietly(self, operation: DecisionOperation, code: str) -> None:
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
