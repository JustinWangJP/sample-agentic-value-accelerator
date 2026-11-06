from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.catalog import CATALOG_RECORD_TYPES
from registry_portal.domain.drafts import (
    AWS_FAILED_STATUSES,
    DRAFT_STATUS,
    IN_FLIGHT_STATUSES,
    NOT_EDITABLE_STATUSES,
    AgentDraft,
    DraftOperation,
    DraftResult,
    DraftView,
    Management,
    OperationState,
    OperationStatus,
    PrincipalRecord,
    content_hash,
    digest,
    draft_view,
    has_draft,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.services.draft_store import DraftStore, StoreConflict

# AWS が入力や設定を理由に保存を拒否したと確定できるエラー。利用者が入力や設定を直して再実行する
AWS_REJECTED_ERRORS = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ConflictException",
        "ResourceNotFoundException",
        "ServiceQuotaExceededException",
    }
)
# AWS が混雑を理由に処理せず拒否したエラー。入力は正しいため、時間をおいて同じ内容で再実行する
AWS_THROTTLED_ERRORS = frozenset({"ThrottlingException"})

DraftResponse = tuple[int, DraftView | DraftResult | OperationStatus]


class RegistryControlClient(Protocol):
    """下書きの作成・編集が使う Registry 管理 API（agent-registry-control）の操作。"""

    def create_registry_record(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_registry_record(self, *, registryId: str, recordId: str) -> Mapping[str, Any]: ...

    def list_registry_records(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def update_registry_record(self, **kwargs: Any) -> Mapping[str, Any]: ...


class DraftConflict(ApiError):
    status = 409
    code = "CONTENT_CONFLICT"
    default_message = "内容が変更されたか、別の操作が処理中です。最新の内容を取得してください。"


class DraftUnavailable(ApiError):
    status = 502
    code = "REGISTRY_UNAVAILABLE"
    default_message = (
        "Registry または補助情報を取得できませんでした。時間をおいて再度お試しください。"
    )


class RegistryThrottled(ApiError):
    """AWS が混雑で保存を処理しなかった。書き込みは行われていないため、操作は失敗として確定する。"""

    status = 429
    code = "REGISTRY_THROTTLED"
    default_message = (
        "Registry が混み合っているため保存できませんでした。時間をおいて再度保存してください。"
    )


class DraftsNotConfigured(ApiError):
    """補助テーブルが未設定で、下書きの操作を提供できない。利用者の入力では解決しない。"""

    status = 503
    code = "DRAFTS_NOT_CONFIGURED"
    default_message = "下書きの管理は準備中です。管理者に連絡してください。"


def require_publisher(caller: Caller) -> None:
    if Role.PUBLISHER not in caller.roles or not caller.teams:
        raise ForbiddenError()


def principal_of(caller: Caller) -> PrincipalRecord:
    return {"issuer": caller.principal.issuer, "subject": caller.principal.subject}


def parse_draft(value: Mapping[str, Any]) -> AgentDraft:
    """リクエストのフォーム入力を下書きとして検査する。"""
    try:
        return AgentDraft.parse(value)
    except ValueError as error:
        raise RequestValidationError(str(error)) from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aws_request_id(response: Mapping[str, Any]) -> str | None:
    request_id = response.get("ResponseMetadata", {}).get("RequestId")
    return request_id if isinstance(request_id, str) and request_id else None


class AgentDrafts:
    def __init__(self, registry: str, store: DraftStore, client: RegistryControlClient) -> None:
        self.registry = registry
        self.store = store
        self._registry = client

    def create(self, caller: Caller, body: Mapping[str, Any], key: str) -> DraftResponse:
        require_publisher(caller)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise RequestValidationError(
                "Idempotency-Key は1〜128文字の英数字・_・- で指定してください。"
            )
        draft = parse_draft(body)
        if draft.fields["ownerTeam"] not in caller.teams:
            raise ForbiddenError()
        principal = principal_of(caller)
        op_id = digest([self.registry, principal["issuer"], principal["subject"], key])
        operation: DraftOperation = {
            "operationId": op_id,
            "ownerTeam": draft.fields["ownerTeam"],
            "principal": principal,
            "requestHash": digest(draft.fields),
            "draft": dict(draft.fields),
            "kind": "CREATE",
            "state": "IN_PROGRESS",
            "lockPk": self.store.name_lock_pk(draft.fields["name"]),
            "startedAt": _now(),
        }
        try:
            existing = self.store.get_operation(op_id)
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        try:
            if existing:
                replayed = self._replay_create(existing, operation["requestHash"])
                if replayed:
                    return replayed
                if existing.get("recordId"):
                    return self._verify(existing, 201)
                operation = existing
            else:
                try:
                    self.store.begin(operation)
                except StoreConflict:
                    # 二重クリックなどで同じキーの操作が先に始まっていれば、その操作の結果を返す
                    concurrent = self.store.get_operation(op_id)
                    if concurrent is None:
                        raise DraftConflict() from None
                    return self._replay_create(
                        concurrent, operation["requestHash"]
                    ) or self._pending(concurrent)
            try:
                response = self._registry.create_registry_record(
                    registryId=self.registry,
                    clientToken=op_id,
                    **draft.create_payload(),
                )
            except ClientError as error:
                return self._registry_error(operation, error)
            operation["recordId"] = str(response["recordArn"]).rsplit("/", 1)[1]
            self._record_request_id(operation, response)
            self.store.save_pending(operation)
            return self._verify(operation, 201)
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def _replay_create(self, existing: DraftOperation, request_hash: str) -> DraftResponse | None:
        """同じ Idempotency-Key の操作が確定済みなら、その結果を返す。未確定なら None。"""
        if existing.get("requestHash") != request_hash:
            raise DraftConflict("同じ Idempotency-Key に異なる内容は指定できません。")
        if existing["state"] == "SUCCEEDED":
            return 201, existing["result"]
        if existing["state"] == "FAILED":
            raise DraftConflict(
                "この操作は失敗が確定しています。内容を確認し、新しいキーで再実行してください。"
            )
        return None

    @staticmethod
    def _record_request_id(operation: DraftOperation, response: Mapping[str, Any]) -> None:
        request_id = _aws_request_id(response)
        if request_id:
            operation["awsRequestId"] = request_id

    def _registry_error(self, operation: DraftOperation, error: ClientError) -> DraftResponse:
        """AWS が処理せず拒否したと確定できる失敗は操作を失敗にし、それ以外は結果確認中にする。"""
        code = error.response["Error"]["Code"]
        self._record_request_id(operation, error.response)
        if code in AWS_THROTTLED_ERRORS:
            self.store.abort(operation, "AWS_THROTTLED")
            raise RegistryThrottled() from None
        if code in AWS_REJECTED_ERRORS:
            self.store.abort(operation, "AWS_REJECTED")
            raise RequestValidationError(
                "Registry が下書きの保存を拒否しました。入力と設定を確認してください。"
            ) from None
        return self._pending(operation)

    @staticmethod
    def _status(
        operation: Mapping[str, Any], state: OperationState, message: str
    ) -> OperationStatus:
        return {
            "operationId": operation["operationId"],
            "operationState": state,
            "message": message,
        }

    def _pending(self, operation: Mapping[str, Any]) -> DraftResponse:
        return 202, self._status(
            operation,
            "VERIFYING",
            "結果確認中です。確認が終わるまで、この下書きへの書き込みを停止しています。",
        )

    def _failed(self, operation: Mapping[str, Any]) -> DraftResponse:
        return 200, self._status(
            operation, "FAILED", "操作は失敗しました。内容を確認して再実行してください。"
        )

    def _load_management(self, record_id: str) -> Management | None:
        """管理情報を読む。読めない場合や型が壊れている場合は、所属外や0件として扱わず502にする。"""
        try:
            return self.store.get_management(record_id)
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        except ValueError:
            raise DraftUnavailable(
                "保存された管理情報の形式が正しくありません。管理者に連絡してください。"
            ) from None

    def _management(self, caller: Caller, record_id: str) -> Management:
        require_publisher(caller)
        if not re.fullmatch(r"[A-Za-z0-9]{12}", record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        management = self._load_management(record_id)
        if management is None or not management.is_owned_by(caller.teams):
            raise ForbiddenError()
        return management

    @staticmethod
    def _read_draft(record: Mapping[str, Any], management: Management) -> AgentDraft:
        try:
            return AgentDraft.from_record(record, management.owner_team, management.item)
        except ValueError:
            raise DraftUnavailable(
                "この下書きはフォームで読み取れません。管理者に連絡してください。"
            ) from None

    def get(self, caller: Caller, record_id: str) -> DraftResponse:
        management = self._management(caller, record_id)
        try:
            record = self._registry.get_registry_record(
                registryId=self.registry, recordId=record_id
            )
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        return 200, draft_view(record, management, self._read_draft(record, management))

    def list_drafts(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        require_publisher(caller)
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
            "filters": [{"name": "recordType", "values": list(CATALOG_RECORD_TYPES)}],
        }
        if token is not None:
            params["nextToken"] = token
        try:
            response = self._registry.list_registry_records(**params)
            items = []
            for record in response["registryRecords"]:
                management = self._load_management(record["recordId"])
                if management is None or not management.is_owned_by(caller.teams):
                    continue
                lock = self.store.get_lock(record["recordId"])
                items.append(
                    {
                        "recordId": record["recordId"],
                        "name": record["name"],
                        "recordType": record["recordType"],
                        "displayName": record.get("displayName", record["name"]),
                        "recordVersion": record.get("recordVersion", ""),
                        "ownerTeam": management.owner_team,
                        "status": record["status"],
                        "hasDraft": has_draft(record["status"]),
                        "publishedVersion": management.published_version(record),
                        "operationId": lock["operationId"] if lock else None,
                    }
                )
            return 200, {"items": items, "nextToken": response.get("nextToken")}
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None

    def operation(self, caller: Caller, op_id: str) -> DraftResponse:
        require_publisher(caller)
        if not re.fullmatch(r"[a-f0-9]{32,64}", op_id):
            raise RequestValidationError("操作 ID の形式が正しくありません。")
        try:
            operation = self.store.get_operation(op_id)
        except (BotoCoreError, ClientError):
            # 補助ストアを読めないときは、所有チームを確かめられないので「確認中」とは返さない
            raise DraftUnavailable() from None
        if not operation or operation["ownerTeam"] not in caller.teams:
            raise ForbiddenError()
        if operation["state"] == "SUCCEEDED":
            return 200, operation["result"]
        if operation["state"] == "FAILED":
            return self._failed(operation)
        if not operation.get("recordId"):
            return self._pending(operation)
        try:
            return self._verify(operation, 200)
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def update(self, caller: Caller, record_id: str, body: Mapping[str, Any]) -> DraftResponse:
        management = self._management(caller, record_id)
        if set(body) != {"draft", "expectedContentHash"} or not isinstance(
            body.get("draft"), Mapping
        ):
            raise RequestValidationError("draft と expectedContentHash を指定してください。")
        expected = body.get("expectedContentHash")
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise RequestValidationError(
                "expectedContentHash には取得した内容ハッシュを指定してください。"
            )
        draft = parse_draft(body["draft"])
        if draft.fields["ownerTeam"] != management.owner_team:
            raise ForbiddenError("所有チームは変更できません。")
        operation: DraftOperation = {
            "operationId": uuid4().hex,
            "ownerTeam": management.owner_team,
            "principal": principal_of(caller),
            "kind": "UPDATE",
            "draft": dict(draft.fields),
            "expectedContentHash": expected,
            "state": "IN_PROGRESS",
            "recordId": record_id,
            "lockPk": self.store.record_pk(record_id),
            "startedAt": _now(),
            "management": dict(management.item),
        }
        try:
            self.store.begin(operation)
        except StoreConflict:
            raise DraftConflict() from None
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        try:
            try:
                management = self._management(caller, record_id)
                record = self._registry.get_registry_record(
                    registryId=self.registry, recordId=record_id
                )
                self._check_editable(record, management, draft, expected)
            except ApiError as error:
                # 書き込む前に止めた操作は失敗を確定し、ロックを解除する
                self.store.abort(operation, error.code)
                raise
            operation["management"] = {
                **management.item,
                "publishedVersion": management.published_version(record),
            }
            self.store.save_pending(operation)
            try:
                response = self._registry.update_registry_record(
                    registryId=self.registry,
                    recordId=record_id,
                    **draft.update_payload(record["descriptors"]),
                )
            except ClientError as error:
                return self._registry_error(operation, error)
            self._record_request_id(operation, response)
            return self._verify(operation, 200, update_accepted=True)
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def _check_editable(
        self,
        record: Mapping[str, Any],
        management: Management,
        draft: AgentDraft,
        expected: str,
    ) -> None:
        if (
            content_hash(record, management) != expected
            or record["status"] in NOT_EDITABLE_STATUSES
        ):
            raise DraftConflict()
        if draft.fields["name"] != record["name"]:
            raise RequestValidationError("name は作成後に変更できません。")
        if draft.record_type != record["recordType"]:
            raise RequestValidationError("種別は作成後に変更できません。")
        # フォームで読めないレコードは、フォームの内容で上書きしない
        self._read_draft(record, management)

    def _verify(
        self,
        operation: DraftOperation,
        success_status: int,
        update_accepted: bool = False,
    ) -> DraftResponse:
        """AWS のレコードを読み、操作の結果を確定する。確定できなければ結果確認中を返す。

        update_accepted は、更新 API が成功を返した直後の確認であることを示す。
        このときは、更新前の内容が見えても「適用されなかった」とは判定しない。
        """
        record = self._registry.get_registry_record(
            registryId=self.registry, recordId=operation["recordId"]
        )
        status = record["status"]
        if status in IN_FLIGHT_STATUSES:
            return self._pending(operation)
        if status in AWS_FAILED_STATUSES:
            self.store.abort(operation, status)
            return self._failed(operation)
        draft = AgentDraft.parse(operation["draft"])
        if status == DRAFT_STATUS and draft.is_applied_to(record):
            return success_status, self._finish(operation, record, draft)
        if (
            operation["kind"] == "UPDATE"
            and not update_accepted
            and content_hash(record, Management(operation["management"]))
            == operation["expectedContentHash"]
        ):
            # 更新前の内容のまま処理中でもないので、更新は AWS に届かなかったと確定する
            self.store.abort(operation, "NOT_APPLIED")
            return self._failed(operation)
        return self._pending(operation)

    def _finish(
        self, operation: DraftOperation, record: Mapping[str, Any], draft: AgentDraft
    ) -> DraftResult:
        management = Management.after_edit(
            operation.get("management", {}),
            operation["ownerTeam"],
            draft,
            operation["principal"],
            _now(),
        )
        result: DraftResult = {
            **draft_view(record, management, draft),
            "operationId": operation["operationId"],
            "operationState": "SUCCEEDED",
        }
        self.store.finish(operation, result, management)
        return result
