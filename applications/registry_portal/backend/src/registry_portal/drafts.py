"""Publisher の下書き操作。Registry の内容と補助ストアの所有情報を結合する。"""

from __future__ import annotations

import re
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.drafts import (
    AgentDraft,
    DEFINITION_FIELDS,
    content_hash,
    digest,
    version_is_greater,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.services.draft_store import DraftStore, StoreConflict


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
        op_id = digest([self.registry, caller.principal.issuer, caller.principal.subject, key])
        operation = {
            "operationId": op_id,
            "ownerTeam": draft.fields["ownerTeam"],
            "principal": {
                "issuer": caller.principal.issuer,
                "subject": caller.principal.subject,
            },
            "requestHash": digest(draft.fields),
            "draft": dict(draft.fields),
            "kind": "CREATE",
            "state": "IN_PROGRESS",
            "lockPk": f"REGISTRY#{self.registry}#NAME#{draft.fields['name']}",
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            existing = self.store.get(f"OP#{op_id}", "CONTROL")
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        try:
            if existing:
                if existing["requestHash"] != operation["requestHash"]:
                    raise DraftConflict("同じ Idempotency-Key に異なる内容は指定できません。")
                if existing["state"] == "SUCCEEDED":
                    return 201, existing["result"]
                if existing["state"] == "FAILED":
                    raise DraftConflict(
                        "この操作は失敗が確定しています。内容を確認し、新しいキーで再実行してください。"
                    )
                if existing.get("recordId"):
                    return self._verify(existing)
                operation = existing
            else:
                try:
                    self.store.begin(operation)
                except StoreConflict:
                    concurrent = self.store.get(f"OP#{op_id}", "CONTROL")
                    if concurrent and concurrent["requestHash"] == operation["requestHash"]:
                        return self._pending(concurrent)
                    raise DraftConflict() from None
            try:
                response = self._client().create_registry_record(
                    registryId=self.registry,
                    clientToken=op_id,
                    **draft.create_payload(),
                )
            except ClientError as error:
                if error.response["Error"]["Code"] in (
                    "ValidationException",
                    "AccessDeniedException",
                    "ConflictException",
                    "ResourceNotFoundException",
                ):
                    self.store.abort(operation, "AWS_REJECTED")
                    raise RequestValidationError(
                        "Registry が下書きの保存を拒否しました。入力と設定を確認してください。"
                    ) from None
                return self._pending(operation)
            operation["recordId"] = response["recordArn"].rsplit("/", 1)[1]
            self.store.save_pending(operation)
            return self._verify(operation)
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def _pending(self, operation: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        return 202, {
            "operationId": operation["operationId"],
            "operationState": "VERIFYING",
            "message": "結果確認中です。確認が終わるまで、この下書きへの書き込みを停止しています。",
        }

    def _management(self, caller: Caller, record_id: str) -> dict[str, Any]:
        require_publisher(caller)
        if not re.fullmatch(r"[A-Za-z0-9]{12}", record_id):
            raise RequestValidationError("Registry レコード ID の形式が正しくありません。")
        try:
            management = self.store.get(self.store.record_pk(record_id), "MANAGEMENT")
        except (BotoCoreError, ClientError):
            raise DraftUnavailable() from None
        if (
            not management
            or management.get("managed") is not True
            or management.get("ownerTeam") not in caller.teams
        ):
            raise ForbiddenError()
        return management

    def _detail(self, record: Mapping[str, Any], management: Mapping[str, Any]) -> dict[str, Any]:
        try:
            if record["recordType"] != "AGENT" or set(record["descriptors"]) != {"custom"}:
                raise ValueError()
            definition = json.loads(record["descriptors"]["custom"]["data"])
            if not isinstance(definition, Mapping) or set(definition) != set(DEFINITION_FIELDS):
                raise ValueError()
            draft = AgentDraft.parse(
                {
                    **{
                        key: record[key]
                        for key in (
                            "name",
                            "displayName",
                            "description",
                            "recordVersion",
                        )
                    },
                    "ownerTeam": management["ownerTeam"],
                    "environment": record["customMetadata"]["environment"],
                    "dataClassification": record["customMetadata"]["dataClassification"],
                    **definition,
                    **{
                        key: management[key]
                        for key in ("contact", "termsOfUse", "connectionReference")
                        if key in management
                    },
                }
            )
        except (KeyError, TypeError, ValueError):
            raise DraftUnavailable(
                "この下書きは custom Agent フォームで読み取れません。管理者に連絡してください。"
            ) from None
        return {
            "recordId": record["recordId"],
            "draft": dict(draft.fields),
            "contentHash": content_hash(record, management),
            "status": record["status"],
            "hasDraft": record["status"] not in ("APPROVED", "DEPRECATED"),
            "publishedVersion": (
                record["recordVersion"]
                if record["status"] == "APPROVED"
                else management.get("publishedVersion")
            ),
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

    def list(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
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
            "filters": [{"name": "recordType", "values": ["AGENT"]}],
        }
        if token is not None:
            params["nextToken"] = token
        try:
            response = self._client().list_registry_records(**params)
            items = []
            for record in response["registryRecords"]:
                management = self.store.get(self.store.record_pk(record["recordId"]), "MANAGEMENT")
                if (
                    not management
                    or management.get("managed") is not True
                    or management.get("ownerTeam") not in caller.teams
                ):
                    continue
                lock = self.store.get(self.store.record_pk(record["recordId"]), "LOCK")
                items.append(
                    {
                        "recordId": record["recordId"],
                        "name": record["name"],
                        "displayName": record.get("displayName", record["name"]),
                        "recordVersion": record.get("recordVersion", ""),
                        "ownerTeam": management["ownerTeam"],
                        "status": record["status"],
                        "hasDraft": record["status"] not in ("APPROVED", "DEPRECATED"),
                        "publishedVersion": (
                            record.get("recordVersion")
                            if record["status"] == "APPROVED"
                            else management.get("publishedVersion")
                        ),
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
            operation = self.store.get(f"OP#{op_id}", "CONTROL")
            if not operation or operation["ownerTeam"] not in caller.teams:
                raise ForbiddenError()
            if operation["state"] == "SUCCEEDED":
                return 200, operation["result"]
            if operation["state"] == "FAILED":
                return 200, {
                    "operationId": op_id,
                    "operationState": "FAILED",
                    "message": "操作は失敗しました。内容を確認して再実行してください。",
                }
            if operation.get("recordId"):
                status, result = self._verify(operation)
                return (200 if status == 201 else status), result
            return self._pending(operation)
        except (BotoCoreError, ClientError):
            return self._pending({"operationId": op_id})

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
        operation = {
            "operationId": uuid4().hex,
            "ownerTeam": management["ownerTeam"],
            "principal": {
                "issuer": caller.principal.issuer,
                "subject": caller.principal.subject,
            },
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
            management = self._management(caller, record_id)
            record = self._client().get_registry_record(
                registryId=self.registry, recordId=record_id
            )
            if content_hash(record, management) != expected or record["status"] in (
                "CREATING",
                "UPDATING",
                "DEPRECATED",
            ):
                self.store.abort(operation, "CONTENT_CONFLICT")
                raise DraftConflict()
            if draft.fields["name"] != record["name"]:
                self.store.abort(operation, "IMMUTABLE_NAME")
                raise RequestValidationError("name は作成後に変更できません。")
            self._detail(record, management)
            published = (
                record.get("recordVersion")
                if record["status"] == "APPROVED"
                else management.get("publishedVersion")
            )
            if published and not version_is_greater(draft.fields["recordVersion"], published):
                self.store.abort(operation, "INVALID_VERSION")
                raise RequestValidationError("recordVersion は公開中の版より大きくしてください。")
            operation["management"] = {**management, "publishedVersion": published}
            self.store.save_pending(operation)
            self._client().update_registry_record(
                registryId=self.registry, recordId=record_id, **draft.update_payload()
            )
            status, result = self._verify(operation)
            return (200 if status == 201 else status), result
        except (BotoCoreError, ClientError, StoreConflict):
            return self._pending(operation)

    def _verify(self, operation: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        record = self._client().get_registry_record(
            registryId=self.registry, recordId=operation["recordId"]
        )
        if record["status"] in ("CREATING", "UPDATING"):
            return self._pending(operation)
        draft = AgentDraft.parse(operation["draft"])
        if (
            content_hash(record) != content_hash(draft.create_payload())
            or record["status"] != "DRAFT"
        ):
            return self._pending(operation)
        management = {
            **operation.get("management", {}),
            "managed": True,
            "ownerTeam": operation["ownerTeam"],
            **{key: draft.fields[key] for key in ("contact", "termsOfUse", "connectionReference")},
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
        return 201, result
