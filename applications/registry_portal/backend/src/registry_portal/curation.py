"""Curator の決裁（公開承認・却下・公開停止・廃止）の実行。

決裁は決裁 handler だけで行う。クライアントから AWS の status は受け取らず、
API ごと（approve / reject / suspend / deprecate）に遷移先を決める。手順は設計書「内容を確認してから決裁する」に従う。

1. レコード単位のロックを取り、決裁の意図を記録する
2. 現物を照合する（公開承認・却下は申請スナップショットのハッシュ・内容ハッシュ・自己承認、
   公開停止・廃止は状態と内容ハッシュ）
3. AWS の UpdateRegistryRecordStatus を呼ぶ
4. 再取得して確認してから成功を記録する。AWS が失敗したら反映済みと表示しない

審査待ちの一覧と審査の詳細は review_queue.py、公開管理の照会は lifecycle.py が受け持つ。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import (
    CuratedRecordNotFound,
    CurationReads,
    DecisionUnavailable,
    PendingApprovalNotFound,
    RegistryRecordMissing,
    RegistryStatusClient,
    display_name,
    is_managed,
    record_state,
    require_curator,
)
from registry_portal.domain.curation import (
    ATTACHMENT_AVAILABLE,
    DECISION_PRECONDITIONS,
    DECISION_TARGETS,
    LIFECYCLE_KINDS,
    DecisionInput,
    LifecycleInput,
    SelfApprovalError,
    check_not_self_approval,
    published_item,
)
from registry_portal.domain.deprecation import RESOLUTION_DEPRECATED, DeprecationRequest
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    DEPRECATE_KIND,
    IN_FLIGHT_STATUSES,
    PENDING_APPROVAL_STATUS,
    RECORD_ID_PATTERN,
    SUSPEND_KIND,
    SUSPENDED_VERSION_KEY,
    DecisionOperation,
    Management,
    OperationKind,
    content_hash,
    digest,
)
from registry_portal.domain.submissions import recompute_snapshot_hash
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


class DecisionConflict(ApiError):
    """審査対象が変わったか、決裁できる状態ではない。再読み込みして確認する。"""

    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "審査対象が変更されたか、別の操作が処理中です。最新の内容を確認してください。"


class DecisionThrottled(ApiError):
    """AWS が混雑で決裁を処理しなかった。書き込みは行われていないため、失敗として確定する。"""

    status = 429
    code = "REGISTRY_THROTTLED"
    default_message = (
        "Registry が混み合っているため決裁できませんでした。時間をおいて再度実行してください。"
    )


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


class RecordCuration:
    """公開承認・却下・公開停止・廃止の決裁の実行と、その結果の確定。"""

    def __init__(self, registry: str, store: DraftStore, client: RegistryStatusClient) -> None:
        self.registry = registry
        self.store = store
        self.reads = CurationReads(registry, store, client)
        self._registry_client = client

    def decide(
        self,
        caller: Caller,
        record_id: str,
        body: Mapping[str, Any],
        key: str,
        *,
        kind: OperationKind,
    ) -> DecisionResponse:
        """公開承認（APPROVE）・却下（REJECT）・公開停止（SUSPEND）・廃止（DEPRECATE）。

        AWS の status はクライアントから受け取らない。公開停止と廃止は申請スナップショットではなく
        公開中の現物を対象にする（設計書「業務上の状態」）。
        """
        require_curator(caller)
        check_idempotency_key(key)
        lifecycle = kind in LIFECYCLE_KINDS
        try:
            if lifecycle:
                reason = LifecycleInput.parse(body, kind).reason
            else:
                decision = DecisionInput.parse(body, kind)
                reason = decision.reason
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        management = self.reads.load_management(record_id)
        if not is_managed(management):
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
            "reason": reason,
            "requestHash": digest(body),
        }
        if not lifecycle:
            operation["snapshotHash"] = decision.snapshot_hash
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

    def _get_record(self, operation: DecisionOperation) -> Mapping[str, Any]:
        """決裁の対象のレコードを読む。存在しなければ 404（公開承認・却下は審査待ちが見つからない）。"""
        try:
            return self.reads.get_record(operation["recordId"])
        except RegistryRecordMissing:
            if operation["kind"] in LIFECYCLE_KINDS:
                raise CuratedRecordNotFound() from None
            raise PendingApprovalNotFound() from None

    def observe_record_status(self, record_id: str) -> str | None:
        """Registry のレコードを再取得し、AWS の状態を返す。削除済み（404）は None（読めた結果）。

        Admin の回復操作が、Registry の状態を見ないまま強制解除しないための確認。
        読めなければ DecisionUnavailable（502）。
        """
        try:
            record = self._registry_client.get_registry_record(
                registryId=self.registry, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise DecisionUnavailable() from None
        except BotoCoreError:
            raise DecisionUnavailable() from None
        return str(record["status"])

    def _check_and_pin(self, operation: DecisionOperation) -> Mapping[str, Any]:
        """現物と申請スナップショットを照合し、決裁の対象を操作に固定する。

        Curator が見たスナップショットのハッシュが、保存済みスナップショットから計算し直したハッシュと
        一致し、現物から計算し直した内容ハッシュも申請時と一致しなければ、決裁しない
        （設計書「内容を確認してから決裁する」）。公開停止・廃止は申請を対象としないため、現物の状態と
        内容ハッシュだけを固定する。固定した時点の状態は、照合で決裁が未反映かを見分けるために残す。
        """
        record_id = operation["recordId"]
        record = self._get_record(operation)
        management = Management(operation["management"])
        try:
            DECISION_PRECONDITIONS[operation["kind"]](record_state(record, management))
        except ValueError as error:
            raise DecisionConflict(str(error)) from None
        operation["observedStatus"] = str(record["status"])
        if operation["kind"] in LIFECYCLE_KINDS:
            # 公開停止・廃止の対象は現物。内容ハッシュで固定し、決裁の照合に使う
            operation["expectedContentHash"] = content_hash(record, management)
            operation["recordVersion"] = str(record["recordVersion"])
            return record
        state, submission_id, submission = self.reads.active_submission(record_id, management)
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
        # 受け付けを記録してから照合する。反映待ちのまま止まっても、AWS に届いた決裁を
        # 再送の要否や Admin の強制解除の判断で「届いたか不明」と扱わないため
        self.store.save_pending(operation)
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
        # 決裁の対象を固定した時点の状態。記録より前の操作（公開承認・却下だけ）は承認待ちだった
        observed = operation.get("observedStatus", PENDING_APPROVAL_STATUS)
        record = self._get_record(operation)
        status = str(record["status"])
        if status == observed and not operation.get("decisionAccepted"):
            if not allow_reissue:
                # 管理 handler の操作照会は決裁の権限を持たないため、AWS を呼び直さない
                return _pending(operation)
            try:
                self._send_decision(operation)
            except ClientError:
                # 先の呼び出しが届いていた可能性があるため、失敗とは確定しない
                pass
            self.store.save_pending(operation)
            record = self._get_record(operation)
            status = str(record["status"])
        if status in IN_FLIGHT_STATUSES:
            return _pending(operation)
        if status == observed:
            # AWS の受け付けを確認した後も決裁前の状態が見えるときは、反映を待っている。
            # 失敗とは確定せず、ロックも解除しない（ADR-0010。確定できなければ ADR-0011 の回復操作）
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
        """決裁の成功を確定する。

        公開承認では、Curator が確認した内容を公開版として保存し、公開停止の印を消す。
        公開停止では公開停止の印（公開中だった版）を管理情報に残す。公開版スナップショットと
        publishedVersion は書き換えず、再申請の semver の下限として保持する。
        廃止では、未解決の廃止依頼を同じトランザクションで解決済みにする。
        """
        published: Mapping[str, Any] | None = None
        management_item = {**management.item, "activeSubmissionId": None}
        kind = operation["kind"]
        if kind == APPROVE_KIND:
            submission = self.reads.get_submission(
                operation["recordId"], str(operation["submissionId"])
            )
            if submission is None:
                raise DecisionUnavailable("申請スナップショットを読み取れません。")
            published = published_item(submission, operation["principal"], now())
            management_item["publishedVersion"] = operation["recordVersion"]
            management_item[SUSPENDED_VERSION_KEY] = None
        elif kind == SUSPEND_KIND:
            management_item[SUSPENDED_VERSION_KEY] = operation["recordVersion"]
        resolved_request = self._resolved_request(operation)
        after = Management(management_item)
        lifecycle = kind in LIFECYCLE_KINDS
        result: dict[str, Any] = {
            "recordId": operation["recordId"],
            "name": record["name"],
            "displayName": display_name(record),
            "recordType": record["recordType"],
            "recordVersion": record["recordVersion"],
            "status": record["status"],
            "ownerTeam": operation["ownerTeam"],
            "publishedVersion": after.published_version(record),
            "operationId": operation["operationId"],
            "operationState": "SUCCEEDED",
            # 公開停止・廃止は申請を対象としないため、申請の情報は持たない
            "submissionId": None if lifecycle else operation["submissionId"],
            "snapshotHash": None if lifecycle else operation["snapshotHash"],
        }
        self.store.finish_decision(operation, result, after, published, resolved_request)
        return 200, result

    def _resolved_request(self, operation: DecisionOperation) -> DeprecationRequest | None:
        """廃止で解決する未解決の廃止依頼。なければ None（トランザクションに入れない）。

        公開停止は依頼を閉じない。廃止するか見送るかの判断が残るよう、依頼は審査待ちの一覧に残す。
        """
        if operation["kind"] != DEPRECATE_KIND:
            return None
        request = self.reads.open_deprecation_request(operation["recordId"])
        if request is None:
            return None
        return request.resolve(
            RESOLUTION_DEPRECATED, operation["reason"], operation["principal"], now()
        )

    def _abort_quietly(self, operation: DecisionOperation, code: str) -> None:
        try:
            self.store.abort(operation, code)
        except (BotoCoreError, ClientError, StoreConflict):
            # 失敗を確定できなければロックは残り、結果確認中として扱われる
            pass
