from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.catalog import AGENT_RECORD_TYPE
from registry_portal.domain.drafts import (
    AWS_FAILED_STATUSES,
    DEFINITION_FIELDS,
    IN_FLIGHT_STATUSES,
    NOT_EDITABLE_STATUSES,
    SUPPLEMENT_FIELDS,
    AgentDraft,
    DraftOperation,
    PrincipalRecord,
    content_hash,
    digest,
    has_draft,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.services.draft_store import DraftStore, StoreConflict


AWS_REJECTED_ERRORS = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ConflictException",
        "ResourceNotFoundException",
        "ThrottlingException",
        "ServiceQuotaExceededException",
    }
)


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


def require_publisher(caller: Caller) -> None:
    if Role.PUBLISHER not in caller.roles or not caller.teams:
        raise ForbiddenError()


def validate_management(management: Mapping[str, Any] | None) -> None:
    if management is None:
        return
    if (
        not isinstance(management.get("managed"), bool)
        or not isinstance(management.get("ownerTeam"), str)
        or not management["ownerTeam"]
        or any(
            key in management and not isinstance(management[key], str) for key in SUPPLEMENT_FIELDS
        )
        or (
            management.get("publishedVersion") is not None
            and not isinstance(management["publishedVersion"], str)
        )
    ):
        raise DraftUnavailable(
            "保存された管理情報の形式が正しくありません。管理者に連絡してください。"
        )


def is_owned_by(management: Mapping[str, Any] | None, caller: Caller) -> bool:
    """ポータルの管理下にあり、所有チームが呼び出し元の所属チームに含まれるか。"""
    return (
        management is not None
        and management.get("managed") is True
        and management.get("ownerTeam") in caller.teams
    )


def published_version(record: Mapping[str, Any], management: Mapping[str, Any]) -> str | None:
    version = (
        record.get("recordVersion")
        if record["status"] == "APPROVED"
        else management.get("publishedVersion")
    )
    if version is not None and not isinstance(version, str):
        raise DraftUnavailable()
    return version


def principal_of(caller: Caller) -> PrincipalRecord:
    return {"issuer": caller.principal.issuer, "subject": caller.principal.subject}


class AgentDrafts:
    def __init__(self, registry: str, region: str, store: DraftStore, client: Any = None) -> None:
        self.registry = registry
        self.region = region
        self.store = store
        self.client = client

    def _client(self) -> Any:
        if self.client is None:
            self.client = boto3.client("agent-registry-control", region_name=self.region)
        return self.client

    def create(
        self, caller: Caller, body: Mapping[str, Any], key: str
    ) -> tuple[int, dict[str, Any]]:
        require_publisher(caller)
        if not self.store.table:
            raise RequestValidationError(
                "下書きの補助テーブルが未設定です。管理者に連絡してください。"
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise RequestValidationError(
                "Idempotency-Key は1〜128文字の英数字・_・- で指定してください。"
            )
        try:
            draft = AgentDraft.parse(body)
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
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
            "startedAt": datetime.now(timezone.utc).isoformat(),
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
                response = self._client().create_registry_record(
                    registryId=self.registry,
                    clientToken=op_id,
                    **draft.create_payload(),
                )
            except ClientError as error:
                return self._registry_error(operation, error)
            operation["recordId"] = response["recordArn"].rsplit("/", 1)[1]
            self.store.save_pending(operation)
            return self._verify(operation, 201)
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def _replay_create(
        self, existing: Mapping[str, Any], request_hash: str
    ) -> tuple[int, dict[str, Any]] | None:
        """同じ Idempotency-Key の操作が確定済みなら、その結果を返す。未確定なら None。"""
        if existing["requestHash"] != request_hash:
            raise DraftConflict("同じ Idempotency-Key に異なる内容は指定できません。")
        if existing["state"] == "SUCCEEDED":
            return 201, existing["result"]
        if existing["state"] == "FAILED":
            raise DraftConflict(
                "この操作は失敗が確定しています。内容を確認し、新しいキーで再実行してください。"
            )
        return None

    def _registry_error(
        self, operation: DraftOperation, error: ClientError
    ) -> tuple[int, dict[str, Any]]:
        """AWS が拒否したと確定できる失敗は操作を失敗にし、それ以外は結果確認中にする。"""
        if error.response["Error"]["Code"] in AWS_REJECTED_ERRORS:
            self.store.abort(operation, "AWS_REJECTED")
            raise RequestValidationError(
                "Registry が下書きの保存を拒否しました。入力と設定を確認してください。"
            ) from None
        return self._pending(operation)

    def _pending(self, operation: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        return 202, {
            "operationId": operation["operationId"],
            "operationState": "VERIFYING",
            "message": "結果確認中です。確認が終わるまで、この下書きへの書き込みを停止しています。",
        }

    def _failed(self, operation: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {
            "operationId": operation["operationId"],
            "operationState": "FAILED",
            "message": "操作は失敗しました。内容を確認して再実行してください。",
        }

    def _management(self, caller: Caller, record_id: str) -> dict[str, Any]:
        require_publisher(caller)
        if not re.fullmatch(r"[A-Za-z0-9]{12}", record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            management = self.store.get_management(record_id)
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        validate_management(management)
        if management is None or not is_owned_by(management, caller):
            raise ForbiddenError()
        return management

    def _read_draft(self, record: Mapping[str, Any], owner_team: str) -> AgentDraft:
        """Registry レコードを custom Agent フォームの下書きとして読む。

        定義は custom descriptor の中身を正本とし、所有チームだけは補助ストアの値を使う（ADR-0002）。
        """
        try:
            if record["recordType"] != AGENT_RECORD_TYPE or set(record["descriptors"]) != {
                "custom"
            }:
                raise ValueError()
            definition = json.loads(record["descriptors"]["custom"]["data"])
            if not isinstance(definition, Mapping) or set(definition) != set(DEFINITION_FIELDS):
                raise ValueError()
            return AgentDraft.parse(
                {
                    **{
                        key: record[key]
                        for key in ("name", "displayName", "description", "recordVersion")
                    },
                    "ownerTeam": owner_team,
                    "environment": record["customMetadata"]["environment"],
                    "dataClassification": record["customMetadata"]["dataClassification"],
                    **definition,
                }
            )
        except (KeyError, TypeError, ValueError):
            raise DraftUnavailable(
                "この下書きは custom Agent フォームで読み取れません。管理者に連絡してください。"
            ) from None

    def _detail(self, record: Mapping[str, Any], management: Mapping[str, Any]) -> dict[str, Any]:
        draft = self._read_draft(record, management["ownerTeam"])
        return {
            "recordId": record["recordId"],
            "draft": dict(draft.fields),
            "contentHash": content_hash(record, management),
            "status": record["status"],
            "hasDraft": has_draft(record["status"]),
            "publishedVersion": published_version(record, management),
        }

    def get(self, caller: Caller, record_id: str) -> tuple[int, dict[str, Any]]:
        management = self._management(caller, record_id)
        try:
            record = self._client().get_registry_record(
                registryId=self.registry, recordId=record_id
            )
            return 200, self._detail(record, management)
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None

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
            "filters": [{"name": "recordType", "values": [AGENT_RECORD_TYPE]}],
        }
        if token is not None:
            params["nextToken"] = token
        try:
            response = self._client().list_registry_records(**params)
            items = []
            for record in response["registryRecords"]:
                management = self.store.get_management(record["recordId"])
                validate_management(management)
                if management is None or not is_owned_by(management, caller):
                    continue
                lock = self.store.get_lock(record["recordId"])
                items.append(
                    {
                        "recordId": record["recordId"],
                        "name": record["name"],
                        "displayName": record.get("displayName", record["name"]),
                        "recordVersion": record.get("recordVersion", ""),
                        "ownerTeam": management["ownerTeam"],
                        "status": record["status"],
                        "hasDraft": has_draft(record["status"]),
                        "publishedVersion": published_version(record, management),
                        "operationId": lock["operationId"] if lock else None,
                    }
                )
            return 200, {"items": items, "nextToken": response.get("nextToken")}
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None

    def operation(self, caller: Caller, op_id: str) -> tuple[int, dict[str, Any]]:
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

    def update(
        self, caller: Caller, record_id: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
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
        try:
            draft = AgentDraft.parse(body["draft"])
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        if draft.fields["ownerTeam"] != management["ownerTeam"]:
            raise ForbiddenError("所有チームは変更できません。")
        operation: DraftOperation = {
            "operationId": uuid4().hex,
            "ownerTeam": management["ownerTeam"],
            "principal": principal_of(caller),
            "kind": "UPDATE",
            "draft": dict(draft.fields),
            "expectedContentHash": expected,
            "state": "IN_PROGRESS",
            "recordId": record_id,
            "lockPk": self.store.record_pk(record_id),
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "management": management,
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
                record = self._client().get_registry_record(
                    registryId=self.registry, recordId=record_id
                )
                self._check_editable(record, management, draft, expected)
                published = published_version(record, management)
            except ApiError as error:
                # 書き込む前に止めた操作は失敗を確定し、ロックを解除する
                self.store.abort(operation, error.code)
                raise
            operation["management"] = {**management, "publishedVersion": published}
            self.store.save_pending(operation)
            try:
                self._client().update_registry_record(
                    registryId=self.registry, recordId=record_id, **draft.update_payload()
                )
            except ClientError as error:
                return self._registry_error(operation, error)
            return self._verify(operation, 200, update_accepted=True)
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def _check_editable(
        self,
        record: Mapping[str, Any],
        management: Mapping[str, Any],
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
        # フォームで読めないレコードは、フォームの内容で上書きしない
        self._read_draft(record, management["ownerTeam"])

    def _verify(
        self,
        operation: DraftOperation,
        success_status: int,
        update_accepted: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """AWS のレコードを読み、操作の結果を確定する。確定できなければ結果確認中を返す。

        update_accepted は、更新 API が成功を返した直後の確認であることを示す。
        このときは、更新前の内容が見えても「適用されなかった」とは判定しない。
        """
        record = self._client().get_registry_record(
            registryId=self.registry, recordId=operation["recordId"]
        )
        status = record["status"]
        if status in IN_FLIGHT_STATUSES:
            return self._pending(operation)
        if status in AWS_FAILED_STATUSES:
            self.store.abort(operation, status)
            return self._failed(operation)
        draft = AgentDraft.parse(operation["draft"])
        if status == "DRAFT" and content_hash(record) == content_hash(draft.create_payload()):
            return success_status, self._finish(operation, record, draft)
        if (
            operation["kind"] == "UPDATE"
            and not update_accepted
            and content_hash(record, operation["management"]) == operation["expectedContentHash"]
        ):
            # 更新前の内容のまま処理中でもないので、更新は AWS に届かなかったと確定する
            self.store.abort(operation, "NOT_APPLIED")
            return self._failed(operation)
        return self._pending(operation)

    def _finish(
        self, operation: DraftOperation, record: Mapping[str, Any], draft: AgentDraft
    ) -> dict[str, Any]:
        management = {
            **operation.get("management", {}),
            "managed": True,
            "ownerTeam": operation["ownerTeam"],
            **draft.supplement(),
        }
        result = {
            "recordId": record["recordId"],
            "draft": dict(draft.fields),
            "contentHash": content_hash(record, management),
            "status": record["status"],
            "hasDraft": True,
            "publishedVersion": management.get("publishedVersion"),
            "operationId": operation["operationId"],
            "operationState": "SUCCEEDED",
        }
        self.store.finish(operation, result, management)
        return result
