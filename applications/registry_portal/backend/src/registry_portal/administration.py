"""Admin の一覧・接続診断と、補助ストア内で完結する取り込み・所有変更。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.config import RegistrySettings
from registry_portal.curation_reads import (
    CurationReads,
    RECORD_BUSY,
    RegistryUnavailable,
    record_summary,
)
from registry_portal.domain.administration import (
    AdministrationResult,
    AdminRecordSummary,
    ConnectionReport,
    OwnershipInput,
    adopted_snapshot,
    adopted_supplement,
    management_hash,
)
from registry_portal.domain.catalog import (
    AGENT_RECORD_TYPE,
    A2A_DESCRIPTOR_TYPE,
    is_catalog_record_type,
)
from registry_portal.domain.drafts import (
    IN_FLIGHT_STATUSES,
    Management,
    OperationKind,
    content_hash,
    digest,
)
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import check_idempotency_key, check_record_id, now, principal_of
from registry_portal.services.discovery import DiscoveryClient, DiscoveryUnavailable
from registry_portal.services.supplement_store import (
    InstantOperation,
    StoreConflict,
    SupplementStore,
)


class RegistryAdministrationClient(Protocol):
    def get_registry(self, *, registryId: str) -> Mapping[str, Any]: ...


class AdministrationConflict(ApiError):
    status = 409
    code = "ADMINISTRATION_CONFLICT"
    default_message = "管理情報が変わりました。一覧を再読み込みして確認してください。"


class AdministrationUnavailable(ApiError):
    status = 503
    code = "ADMINISTRATION_UNAVAILABLE"
    default_message = "管理操作の結果を確認できません。同じ内容で再試行して結果を確認してください。"


def require_admin(caller: Caller) -> None:
    if Role.ADMIN not in caller.roles:
        raise ForbiddenError("Registry の管理は Admin のみ行えます。")


class AdministrationReads:
    def __init__(
        self,
        settings: RegistrySettings,
        client: RegistryAdministrationClient,
        reads: CurationReads | None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.reads = reads

    def list_records(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        require_admin(caller)
        if self.reads is None:
            raise RegistryUnavailable("補助テーブルが設定されていません。")
        mode = query.get("management", "unmanaged")
        token = query.get("nextToken")
        if (
            set(query) - {"management", "nextToken"}
            or mode not in {"unmanaged", "managed"}
            or (token is not None and (not token or len(token) > 2048))
        ):
            raise RequestValidationError(
                "management は unmanaged または managed、有効な nextToken を指定してください。"
            )
        params: dict[str, Any] = {"maxResults": 50}
        if token is not None:
            params["nextToken"] = token
        try:
            page = self.reads.list_records(**params)
            items: list[AdminRecordSummary] = []
            for record in page["registryRecords"]:
                if not is_catalog_record_type(record.get("recordType")):
                    continue
                management = self.reads.load_management(record["recordId"])
                managed = management is not None and management.is_managed
                if managed != (mode == "managed"):
                    continue
                items.append(
                    cast(
                        AdminRecordSummary,
                        {
                            **record_summary(record, management),
                            "managementHash": management_hash(management) if management else None,
                        },
                    )
                )
            return 200, {"items": items, "nextToken": page.get("nextToken")}
        except (BotoCoreError, ClientError, KeyError, TypeError):
            raise RegistryUnavailable() from None

    def connection(self, caller: Caller) -> tuple[int, ConnectionReport]:
        require_admin(caller)
        result: ConnectionReport = {
            "registryArn": self.settings.arn,
            "region": self.settings.region,
            "authorization": None,
            "connection": {"state": "CONNECTED"},
            "autoApproval": {"state": "UNKNOWN"},
            "warnings": [],
        }
        try:
            registry = self.client.get_registry(registryId=self.settings.arn)
            authorization = registry.get("discoveryConfiguration", {}).get("authorizerType")
            if authorization not in {"AWS_IAM", "CUSTOM_JWT"}:
                raise ValueError()
            result["authorization"] = authorization
            rules = registry.get("approvalConfiguration", {}).get("autoApprovalRules")
            if not isinstance(rules, list):
                result["warnings"].append(
                    "自動承認の設定を取得できません。申請前に設定を確認してください。"
                )
            else:
                result["autoApproval"] = {"state": "ENABLED" if rules else "DISABLED"}
                if rules:
                    result["warnings"].append(
                        "自動承認の設定が想定から外れています。新規申請は停止されています。"
                    )
        except (BotoCoreError, ClientError, ValueError, TypeError, AttributeError):
            result["connection"] = {
                "state": "ERROR",
                "code": "REGISTRY_CONNECTION_ERROR",
                "message": "Registry の接続情報を取得できません。権限・接続先・サービスの状態を確認してください。",
            }
            result["warnings"] = [
                "自動承認の設定は未取得です。設定を確認してから申請してください。"
            ]
        return 200, result


class RegistryAdministration:
    def __init__(
        self, reads: CurationReads, store: SupplementStore, discovery: DiscoveryClient | None
    ) -> None:
        self.reads, self.store, self.discovery = reads, store, discovery

    def execute(
        self, caller: Caller, record_id: str, body: Mapping[str, Any], key: str, *, reassign: bool
    ) -> tuple[int, AdministrationResult]:
        require_admin(caller)
        check_record_id(record_id)
        check_idempotency_key(key)
        try:
            request = OwnershipInput.parse(body, reassign=reassign)
        except ValueError as error:
            raise RequestValidationError(str(error)) from None
        kind: OperationKind = "REASSIGN_OWNER" if reassign else "ADOPT"
        op_id = digest([kind, self.store.registry, record_id, principal_of(caller), key])
        request_hash = digest(body)
        try:
            receipt = self.store.administration_receipt(op_id)
            if receipt is not None:
                return self._replay(receipt, request_hash)
            management = self.reads.load_management(record_id)
            if reassign:
                if (
                    management is None
                    or not management.is_managed
                    or management_hash(management) != request.expected_hash
                ):
                    raise AdministrationConflict()
            elif management is not None and management.is_managed:
                raise AdministrationConflict(
                    "すでに管理下にあるレコードです。一覧を再読み込みしてください。"
                )
            if self.reads.locking_operation_id(record_id) is not None:
                raise AdministrationConflict(RECORD_BUSY)
            record = self.reads.require_record(record_id)
            if record["status"] in IN_FLIGHT_STATUSES:
                raise AdministrationConflict(
                    "Registry が処理中です。完了後に取り込み・所有変更を行ってください。"
                )
            published = None
            backfill = False
            if reassign:
                assert management is not None
                updated = Management({**management.item, "ownerTeam": request.owner_team})
            else:
                if self.discovery is None:
                    raise RegistryUnavailable(
                        "公開版の取得設定がありません。管理者に連絡してください。"
                    )
                public_record = self.discovery.get_published_record(record_id)
                if record["status"] == "APPROVED" and (
                    public_record is None
                    or public_record["recordVersion"] != record["recordVersion"]
                ):
                    raise AdministrationConflict(
                        "公開版が発見 API に反映されていません。時間をおいて再試行してください。"
                    )
                try:
                    fields: dict[str, Any] = {
                        "managed": True,
                        "adoptedAt": now(),
                        "ownerTeam": request.owner_team,
                        **adopted_supplement(record),
                    }
                    if public_record is not None:
                        fields |= {
                            "publishedVersion": public_record["recordVersion"],
                            "adoptedPublishedVersion": public_record["recordVersion"],
                        }
                        published = adopted_snapshot(public_record)
                        backfill = public_record["recordType"] == AGENT_RECORD_TYPE and (
                            not isinstance(
                                public_record.get("customMetadata", {}).get("isA2A"), bool
                            )
                            or public_record["customMetadata"]["isA2A"]
                            != (A2A_DESCRIPTOR_TYPE in public_record["descriptors"])
                        )
                    updated = Management.parse(fields)
                except ValueError as error:
                    raise RequestValidationError(str(error)) from None
            result: AdministrationResult = {
                "recordId": record_id,
                "ownerTeam": request.owner_team,
                "operationId": op_id,
                "operationState": "SUCCEEDED",
                "publishedVersion": updated.published_version(record),
                "a2aBackfillRequired": backfill,
            }
            audit: InstantOperation = {
                "operationId": op_id,
                "kind": kind,
                "principal": principal_of(caller),
                "startedAt": now(),
                "ownerTeam": request.owner_team,
                "recordId": record_id,
                "reason": request.reason,
                "expectedContentHash": content_hash(record, management),
                "recordVersion": record["recordVersion"],
            }
            if management is not None:
                audit["previousOwnerTeam"] = management.owner_team
            self.store.save_administration(
                management,
                updated,
                published,
                audit,
                {"requestHash": request_hash, "result": result},
            )
            return 200, result
        except (BotoCoreError, ClientError, StoreConflict) as error:
            try:
                receipt = self.store.administration_receipt(op_id)
                if receipt is not None:
                    return self._replay(receipt, request_hash)
            except (BotoCoreError, ClientError):
                raise AdministrationUnavailable() from None
            if isinstance(error, StoreConflict):
                raise AdministrationConflict() from None
            raise AdministrationUnavailable() from None
        except DiscoveryUnavailable:
            raise RegistryUnavailable(
                "公開版を取得できなかったため取り込めません。時間をおいて再試行してください。"
            ) from None

    def _replay(
        self, receipt: Mapping[str, Any], request_hash: str
    ) -> tuple[int, AdministrationResult]:
        if receipt.get("requestHash") != request_hash:
            raise AdministrationConflict("同じ Idempotency-Key に異なる内容は指定できません。")
        return 200, cast(AdministrationResult, receipt["result"])
