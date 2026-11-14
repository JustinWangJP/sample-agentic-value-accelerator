"""Curator の決裁（公開承認・却下）と、公開停止・廃止の実行。

公開停止・廃止は申請への決裁ではないが、AWS の状態更新 API を呼ぶため、決裁と同じ決裁 handler で同じ手順を踏む。
クライアントから AWS の status は受け取らず、API ごと（approve / reject / suspend / deprecate）に遷移先を決める。
手順は設計書「内容を確認してから決裁する」に従う。

1. レコード単位のロックを取り、操作の意図を記録する
2. 現物を照合する（決裁は申請スナップショットのハッシュ・内容ハッシュ・自己承認、
   公開停止・廃止は状態と内容ハッシュ）
3. AWS の UpdateRegistryRecordStatus を呼ぶ
4. 再取得して確認してから成功を記録する。AWS が失敗したら反映済みと表示しない

審査待ちの一覧と審査の詳細は review_queue.py、公開管理の照会は publication.py が受け持つ。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller
from registry_portal.curation_reads import (
    CurationReads,
    PendingApprovalNotFound,
    RecordNotFound,
    RegistryRecordMissing,
    RegistryStatusClient,
    RegistryUnavailable,
    is_managed,
    record_state,
    record_summary,
    require_curator,
)
from registry_portal.domain.curation import (
    ATTACHMENT_AVAILABLE,
    CURATION_RULES,
    DecisionInput,
    PublicationInput,
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
    REJECT_KIND,
    SUSPEND_KIND,
    SUSPENDED_VERSION_KEY,
    DecisionOperation,
    Management,
    OperationKind,
    RecordStatus,
    content_hash,
    digest,
)
from registry_portal.domain.submissions import recompute_snapshot_hash
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import (
    AWS_REJECTED_ERRORS,
    AWS_THROTTLED_ERRORS,
    check_idempotency_key,
    check_record_id,
    now,
    operation_status,
    principal_of,
    record_request_id,
)
from registry_portal.services.supplement_store import SupplementStore, StoreConflict

# 決裁の本文の上限。スナップショットのハッシュ（64文字）と決裁理由（255文字）に余裕を持たせる
DECISION_BODY_MAX_LENGTH = 2048
DecisionResponse = tuple[int, Any]

_SUBMISSION_UNCONFIRMED = (
    "ポータルの申請が確認できないため決裁できません。Publisher に再申請を依頼してください。"
)


class DecisionConflict(ApiError):
    """対象が変わったか、決裁・公開停止・廃止できる状態ではない。再読み込みして確認する。"""

    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "対象が変更されたか、別の操作が処理中です。最新の内容を確認してください。"


class DecisionThrottled(ApiError):
    """AWS が混雑で決裁・公開停止・廃止を処理しなかった。書き込みは行われていないため、失敗として確定する。"""

    status = 429
    code = "REGISTRY_THROTTLED"
    default_message = (
        "Registry が混み合っているため実行できませんでした。時間をおいて再度実行してください。"
    )


@dataclass(frozen=True)
class _Outcome:
    """操作の成功を確定するときに補助テーブルへ書く、種別ごとの変更。"""

    # 管理情報に重ねる項目
    management: Mapping[str, Any] = field(default_factory=dict)
    # 公開承認で保存する公開版スナップショット
    published: Mapping[str, Any] | None = None
    # 廃止で解決済みにする廃止依頼
    resolved_request: DeprecationRequest | None = None


class _CurationSubject(Protocol):
    """決裁 handler の操作の対象。決裁は申請スナップショット、公開停止・廃止は公開中の現物を対象にする。

    種別ごとに異なるのは、本文の検査・現物との照合と固定・見つからないときのエラー・結果の申請の項目・
    成功で補助テーブルに書く変更だけで、ロック・意図の記録・AWS の呼び出し・照合による確定の手順は共通にする。
    """

    def parse(self, body: Mapping[str, Any], kind: OperationKind) -> tuple[str, str | None]:
        """本文を検査し、理由と、審査したスナップショットのハッシュ（決裁のときだけ）を返す。"""
        ...

    def pin(
        self,
        reads: CurationReads,
        operation: DecisionOperation,
        record: Mapping[str, Any],
        management: Management,
    ) -> None:
        """現物を照合し、操作の対象（内容ハッシュと版）を操作に固定する。照合できなければ ApiError。"""
        ...

    def not_found(self) -> ApiError:
        """対象のレコードが Registry にないときのエラー。"""
        ...

    def result_fields(self, operation: DecisionOperation) -> dict[str, Any]:
        """結果に載せる申請の項目。"""
        ...

    def outcome(self, reads: CurationReads, operation: DecisionOperation) -> _Outcome:
        """成功の確定で補助テーブルに書く変更。"""
        ...


class _SubmissionSubject:
    """決裁（公開承認・却下）。Curator が審査した申請スナップショットを対象にする。却下は補助情報を変えない。"""

    def parse(self, body: Mapping[str, Any], kind: OperationKind) -> tuple[str, str | None]:
        decision = DecisionInput.parse(body, kind)
        return decision.reason, decision.snapshot_hash

    def pin(
        self,
        reads: CurationReads,
        operation: DecisionOperation,
        record: Mapping[str, Any],
        management: Management,
    ) -> None:
        """Curator が見たスナップショットのハッシュが、保存済みスナップショットから計算し直したハッシュと
        一致し、現物から計算し直した内容ハッシュも申請時と一致しなければ、決裁しない
        （設計書「内容を確認してから決裁する」）。
        """
        state, submission_id, submission = reads.active_submission(
            operation["recordId"], management
        )
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

    def not_found(self) -> ApiError:
        return PendingApprovalNotFound()

    def result_fields(self, operation: DecisionOperation) -> dict[str, Any]:
        return {
            "submissionId": operation["submissionId"],
            "snapshotHash": operation["snapshotHash"],
        }

    def outcome(self, reads: CurationReads, operation: DecisionOperation) -> _Outcome:
        return _Outcome()


class _Approval(_SubmissionSubject):
    """公開承認。Curator が確認した内容を公開版として保存し、公開停止の印を消す。"""

    def outcome(self, reads: CurationReads, operation: DecisionOperation) -> _Outcome:
        submission = reads.get_submission(operation["recordId"], str(operation["submissionId"]))
        if submission is None:
            raise RegistryUnavailable("申請スナップショットを読み取れません。")
        return _Outcome(
            management={
                "publishedVersion": operation["recordVersion"],
                SUSPENDED_VERSION_KEY: None,
            },
            published=published_item(submission, operation["principal"], now()),
        )


class _LiveRecordSubject:
    """公開停止・廃止。申請ではなく公開中の現物を対象にし、内容ハッシュと版で固定する。"""

    def parse(self, body: Mapping[str, Any], kind: OperationKind) -> tuple[str, str | None]:
        return PublicationInput.parse(body, kind).reason, None

    def pin(
        self,
        reads: CurationReads,
        operation: DecisionOperation,
        record: Mapping[str, Any],
        management: Management,
    ) -> None:
        operation["expectedContentHash"] = content_hash(record, management)
        operation["recordVersion"] = str(record["recordVersion"])

    def not_found(self) -> ApiError:
        return RecordNotFound()

    def result_fields(self, operation: DecisionOperation) -> dict[str, Any]:
        # 申請を対象としないため、申請の情報は持たない
        return {"submissionId": None, "snapshotHash": None}


class _Suspension(_LiveRecordSubject):
    """公開停止。公開停止の印（公開中だった版）を管理情報に残す。

    公開版スナップショットと publishedVersion は書き換えず、再申請の semver の下限として保持する。
    """

    def outcome(self, reads: CurationReads, operation: DecisionOperation) -> _Outcome:
        return _Outcome(management={SUSPENDED_VERSION_KEY: operation["recordVersion"]})


class _Deprecation(_LiveRecordSubject):
    """廃止。未解決の廃止依頼を同じトランザクションで解決済みにする。

    公開停止は依頼を閉じない。廃止するか見送るかの判断が残るよう、依頼は審査待ちの一覧に残す。
    """

    def outcome(self, reads: CurationReads, operation: DecisionOperation) -> _Outcome:
        request = reads.open_deprecation_request(operation["recordId"])
        if request is None:
            # 依頼がなければトランザクションに入れない
            return _Outcome()
        return _Outcome(
            resolved_request=request.resolve(
                RESOLUTION_DEPRECATED, operation["reason"], operation["principal"], now()
            )
        )


_SUBJECTS: Mapping[OperationKind, _CurationSubject] = {
    APPROVE_KIND: _Approval(),
    REJECT_KIND: _SubmissionSubject(),
    SUSPEND_KIND: _Suspension(),
    DEPRECATE_KIND: _Deprecation(),
}


def _pending(operation: Mapping[str, Any]) -> DecisionResponse:
    return 202, operation_status(
        operation,
        "VERIFYING",
        "結果確認中です。確認が終わるまで、このレコードへの書き込みを停止しています。",
    )


def _failed(operation: Mapping[str, Any]) -> DecisionResponse:
    return 200, operation_status(
        operation, "FAILED", "操作は失敗しました。最新の状態を確認して再度実行してください。"
    )


class RecordCuration:
    """決裁（公開承認・却下）と公開停止・廃止の実行と、その結果の確定。"""

    def __init__(
        self, reads: CurationReads, store: SupplementStore, client: RegistryStatusClient
    ) -> None:
        self.registry = reads.registry
        self.store = store
        self.reads = reads
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
        try:
            reason, snapshot_hash = _SUBJECTS[kind].parse(body, kind)
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        check_record_id(record_id)
        management = self.reads.load_management(record_id)
        if not is_managed(management):
            raise DecisionConflict(
                "ポータルで管理されていないレコードは操作できません。管理者に連絡してください。"
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
        if snapshot_hash is not None:
            operation["snapshotHash"] = snapshot_hash
        try:
            existing = self.store.get_decision_operation(operation["operationId"])
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        if existing:
            return self._replay(existing, operation["requestHash"])
        try:
            self.store.begin(operation)
        except StoreConflict:
            raise DecisionConflict() from None
        except (BotoCoreError, ClientError):
            raise RegistryUnavailable() from None
        try:
            try:
                self._check_and_pin(operation)
            except ApiError as error:
                # AWS へ書き込む前に止めた操作は失敗に確定し、ロックを解除する
                self._abort_quietly(operation, error.code)
                raise
            except (BotoCoreError, ClientError):
                self._abort_quietly(operation, RegistryUnavailable.code)
                raise RegistryUnavailable() from None
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
        """操作の対象のレコードを読む。存在しなければ 404（決裁では審査待ちが見つからない）。"""
        try:
            return self.reads.get_record(operation["recordId"])
        except RegistryRecordMissing:
            raise _SUBJECTS[operation["kind"]].not_found() from None

    def observe_record_status(self, record_id: str) -> str | None:
        """Registry のレコードを再取得し、AWS の状態を返す。削除済み（404）は None（読めた結果）。

        Admin の回復操作が、Registry の状態を見ないまま強制解除しないための確認。
        読めなければ RegistryUnavailable（502）。
        """
        try:
            record = self._registry_client.get_registry_record(
                registryId=self.registry, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise RegistryUnavailable() from None
        except BotoCoreError:
            raise RegistryUnavailable() from None
        return str(record["status"])

    def _check_and_pin(self, operation: DecisionOperation) -> None:
        """現物を照合し、操作の対象を操作に固定する（照合の内容は対象ごとに _CurationSubject が決める）。

        固定した時点の状態は、照合で操作が未反映かを見分けるために残す。
        """
        record = self._get_record(operation)
        management = Management(operation["management"])
        try:
            CURATION_RULES[operation["kind"]].precondition(record_state(record, management))
        except ValueError as error:
            raise DecisionConflict(str(error)) from None
        operation["observedStatus"] = cast(RecordStatus, str(record["status"]))
        _SUBJECTS[operation["kind"]].pin(self.reads, operation, record, management)

    def _send_decision(self, operation: DecisionOperation) -> None:
        """意図に記録した決裁（種別・理由）を AWS の状態更新 API に送る。受け付けられたら記録する。

        呼び出し側は、先に送信時刻を記録する（store.record_sent）。補助テーブルのエラーを AWS の
        状態更新 API のエラーと取り違えないよう、ここでは記録しない。
        """
        try:
            response = self._registry_client.update_registry_record_status(
                registryId=self.registry,
                recordId=operation["recordId"],
                status=CURATION_RULES[operation["kind"]].target,
                statusReason=operation["reason"],
            )
        except ClientError as error:
            record_request_id(operation, error.response)
            raise
        record_request_id(operation, response)
        operation["decisionAccepted"] = True

    def _call_status_api(self, operation: DecisionOperation) -> DecisionResponse:
        """AWS の状態更新 API を呼び、結果を照合する。"""
        self.store.record_sent(operation)
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
                "レコードの状態が変わったため実行できませんでした。最新の状態を確認してください。"
            ) from None
        if code in AWS_REJECTED_ERRORS:
            self.store.abort(operation, "AWS_REJECTED")
            raise RequestValidationError(
                "Registry が操作を拒否しました。入力と設定を確認してください。"
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
            raise ForbiddenError("この操作は、実行した Curator だけが参照できます。")
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
            self.store.record_sent(operation)
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
        target = CURATION_RULES[operation["kind"]].target
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
        """決裁・公開停止・廃止の成功を確定する。補助テーブルに書く変更は種別ごとに _CurationSubject が決める。"""
        subject = _SUBJECTS[operation["kind"]]
        outcome = subject.outcome(self.reads, operation)
        after = Management({**management.item, "activeSubmissionId": None, **outcome.management})
        result: dict[str, Any] = {
            **record_summary(record, after),
            "publishedVersion": after.published_version(record),
            "operationId": operation["operationId"],
            "operationState": "SUCCEEDED",
            **subject.result_fields(operation),
        }
        self.store.finish_decision(
            operation, result, after, outcome.published, outcome.resolved_request
        )
        return 200, result

    def _abort_quietly(self, operation: DecisionOperation, code: str) -> None:
        try:
            self.store.abort(operation, code)
        except (BotoCoreError, ClientError, StoreConflict):
            # 失敗を確定できなければロックは残り、結果確認中として扱われる
            pass
