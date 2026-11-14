"""Admin の一覧・接続診断と、補助ストア内で完結する取り込み・所有変更。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, Protocol, cast

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
    MANAGEMENT_FILTERS,
    AdministrationResult,
    AdminRecordSummary,
    ConnectionReport,
    OwnershipInput,
    adopted_snapshot,
    adopted_supplement,
    is_authorizer_type,
)
from registry_portal.domain.catalog import is_catalog_record_type
from registry_portal.domain.descriptors import needs_a2a_backfill
from registry_portal.domain.drafts import (
    ADOPT_KIND,
    IN_FLIGHT_STATUSES,
    PUBLISHED_STATUS,
    REASSIGN_OWNER_KIND,
    Management,
    OperationKind,
    content_hash,
    digest,
)
from registry_portal.domain.submissions import auto_approval_state
from registry_portal.errors import ApiError, ForbiddenError, RequestValidationError
from registry_portal.operations import (
    check_idempotency_key,
    check_next_token,
    check_record_id,
    now,
    principal_of,
)
from registry_portal.services.discovery import DiscoveryClient, DiscoveryUnavailable
from registry_portal.services.supplement_store import (
    AdministrationChange,
    AdministrationReceipt,
    InstantOperation,
    StoreConflict,
    SupplementStore,
)

ADMIN_PAGE_SIZE = 50


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
        if set(query) - {"management", "nextToken"} or mode not in MANAGEMENT_FILTERS:
            raise RequestValidationError(
                "management は unmanaged または managed を指定してください。"
            )
        token = query.get("nextToken")
        check_next_token(token)
        params: dict[str, Any] = {"maxResults": ADMIN_PAGE_SIZE}
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
                            "managementHash": management.fingerprint if management else None,
                        },
                    )
                )
            return 200, {"items": items, "nextToken": page.get("nextToken")}
        except (BotoCoreError, ClientError, KeyError, TypeError):
            raise RegistryUnavailable() from None

    def connection(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, ConnectionReport]:
        """設定由来の ARN・リージョンと、GetRegistry で観測した認可方式・自動承認を返す。

        通信に失敗したときだけ接続エラーとする。応答に任意項目がないときは接続済みのまま、
        その項目を未取得として警告し、値を推測しない。
        """
        require_admin(caller)
        if query:
            raise RequestValidationError("接続情報にクエリパラメーターは指定できません。")
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
        except ClientError as error:
            # 権限・スロットリング・サービス障害などを切り分けられるよう、AWS のエラーコードを返す
            return 200, _disconnected(
                result, error.response.get("Error", {}).get("Code") or "ClientError"
            )
        except BotoCoreError as error:
            # 接続先に届かないなど、AWS の応答がない失敗は例外名で示す
            return 200, _disconnected(result, type(error).__name__)
        if not isinstance(registry, Mapping):
            return 200, _disconnected(result, "InvalidResponse")
        discovery = registry.get("discoveryConfiguration")
        authorization = discovery.get("authorizerType") if isinstance(discovery, Mapping) else None
        if is_authorizer_type(authorization):
            result["authorization"] = authorization
        else:
            result["warnings"].append(
                "認可方式を取得できません。Registry の発見設定を確認してください。"
            )
        state = auto_approval_state(registry)
        result["autoApproval"] = {"state": state}
        if state == "ENABLED":
            result["warnings"].append(
                "自動承認の設定が想定から外れています。新規申請は停止されています。"
            )
        elif state == "UNKNOWN":
            result["warnings"].append(
                "自動承認の設定を取得できません。設定を確認できるまで新規申請は停止されています。"
            )
        return 200, result


def _disconnected(result: ConnectionReport, cause: str) -> ConnectionReport:
    """GetRegistry に失敗した接続情報。設定由来の ARN・リージョンは残し、観測値は未取得とする。"""
    result["connection"] = {
        "state": "ERROR",
        "code": "REGISTRY_CONNECTION_ERROR",
        "message": "Registry の接続情報を取得できません。権限・接続先・サービスの状態を確認してください。",
        "cause": cause,
    }
    result["warnings"].append("自動承認の設定は未取得です。設定を確認してから申請してください。")
    return result


class _SavePlan(NamedTuple):
    """取り込み・所有変更で保存する管理情報と、取り込み時の公開版スナップショット・A2A 補完の要否。"""

    management: Management
    published: dict[str, Any] | None = None
    a2a_backfill_required: bool = False


class _OwnershipChange(Protocol):
    """取り込み・所有変更のそれぞれが持つ、本文の検査・前提の検査・保存内容の決定。"""

    kind: OperationKind

    def parse(self, body: Mapping[str, Any]) -> OwnershipInput:
        """本文を検査する。不正なら ValueError。"""

    def check(self, management: Management | None, request: OwnershipInput) -> None:
        """補助ストアの管理情報だけで判断できる前提。崩れていれば Registry を読む前に 409。"""

    def plan(
        self, record: Mapping[str, Any], management: Management | None, request: OwnershipInput
    ) -> _SavePlan:
        """読取時の Registry レコードと管理情報から、保存する内容を決める。"""


class _Adoption:
    kind: OperationKind = ADOPT_KIND

    def __init__(self, discovery: DiscoveryClient | None) -> None:
        self.discovery = discovery

    def parse(self, body: Mapping[str, Any]) -> OwnershipInput:
        return OwnershipInput.parse_adoption(body)

    def check(self, management: Management | None, request: OwnershipInput) -> None:
        if management is not None and management.is_managed:
            raise AdministrationConflict(
                "すでに管理下にあるレコードです。一覧を再読み込みしてください。"
            )

    def plan(
        self, record: Mapping[str, Any], management: Management | None, request: OwnershipInput
    ) -> _SavePlan:
        if self.discovery is None:
            raise RegistryUnavailable("公開版の取得設定がありません。管理者に連絡してください。")
        public_record = self.discovery.get_published_record(record["recordId"])
        if record["status"] == PUBLISHED_STATUS and (
            public_record is None or public_record["recordVersion"] != record["recordVersion"]
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
            if public_record is None:
                return _SavePlan(Management.parse(fields))
            fields |= {
                "publishedVersion": public_record["recordVersion"],
                "adoptedPublishedVersion": public_record["recordVersion"],
            }
            return _SavePlan(
                Management.parse(fields),
                adopted_snapshot(public_record),
                needs_a2a_backfill(public_record),
            )
        except ValueError as error:
            raise RequestValidationError(str(error)) from None


class _Reassignment:
    kind: OperationKind = REASSIGN_OWNER_KIND

    def parse(self, body: Mapping[str, Any]) -> OwnershipInput:
        return OwnershipInput.parse_reassignment(body)

    def check(self, management: Management | None, request: OwnershipInput) -> None:
        if (
            management is None
            or not management.is_managed
            or management.fingerprint != request.expected_hash
        ):
            raise AdministrationConflict()

    def plan(
        self, record: Mapping[str, Any], management: Management | None, request: OwnershipInput
    ) -> _SavePlan:
        assert management is not None  # check で管理下であることを確かめている
        return _SavePlan(Management({**management.item, "ownerTeam": request.owner_team}))


class RegistryAdministration:
    def __init__(
        self, reads: CurationReads, store: SupplementStore, discovery: DiscoveryClient | None
    ) -> None:
        self.reads, self.store = reads, store
        adoption, reassignment = _Adoption(discovery), _Reassignment()
        self._changes: Mapping[OperationKind, _OwnershipChange] = {
            adoption.kind: adoption,
            reassignment.kind: reassignment,
        }

    def change_ownership(
        self,
        caller: Caller,
        record_id: str,
        body: Mapping[str, Any],
        key: str,
        *,
        kind: OperationKind,
    ) -> tuple[int, AdministrationResult]:
        """取り込み（ADOPT）は未管理レコードを所有チームへ割り当てて管理下に置き、所有変更
        （REASSIGN_OWNER）は管理下のレコードの所有チームだけを変える。どちらも Registry は更新しない。
        """
        change = self._changes[kind]
        request = _validated(caller, record_id, key, change.parse, body)
        op_id = digest([kind, self.store.registry, record_id, principal_of(caller), key])
        request_hash = digest(body)
        try:
            receipt = self.store.administration_receipt(op_id)
            if receipt is not None:
                return _replay(receipt, request_hash)
            management = self.reads.load_management(record_id)
            change.check(management, request)
            if self.reads.locking_operation_id(record_id) is not None:
                raise AdministrationConflict(RECORD_BUSY)
            record = self.reads.require_record(record_id)
            if record["status"] in IN_FLIGHT_STATUSES:
                raise AdministrationConflict(
                    "Registry が処理中です。完了後に取り込み・所有変更を行ってください。"
                )
            planned = change.plan(record, management, request)
            result: AdministrationResult = {
                "recordId": record_id,
                "ownerTeam": request.owner_team,
                "operationId": op_id,
                "operationState": "SUCCEEDED",
                "publishedVersion": planned.management.published_version(record),
                "a2aBackfillRequired": planned.a2a_backfill_required,
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
                AdministrationChange(
                    management,
                    planned.management,
                    planned.published,
                    audit,
                    {"requestHash": request_hash, "result": result},
                )
            )
            return 200, result
        except (BotoCoreError, ClientError, StoreConflict) as error:
            try:
                receipt = self.store.administration_receipt(op_id)
                if receipt is not None:
                    return _replay(receipt, request_hash)
            except (BotoCoreError, ClientError):
                raise AdministrationUnavailable() from None
            if isinstance(error, StoreConflict):
                raise AdministrationConflict() from None
            raise AdministrationUnavailable() from None
        except DiscoveryUnavailable:
            raise RegistryUnavailable(
                "公開版を取得できなかったため取り込めません。時間をおいて再試行してください。"
            ) from None


def _validated(
    caller: Caller,
    record_id: str,
    key: str,
    parse: Callable[[Mapping[str, Any]], OwnershipInput],
    body: Mapping[str, Any],
) -> OwnershipInput:
    """AWS・補助ストアへ問い合わせる前に、権限・path・Idempotency-Key・本文を検査する。"""
    require_admin(caller)
    check_record_id(record_id)
    check_idempotency_key(key)
    try:
        return parse(body)
    except ValueError as error:
        raise RequestValidationError(str(error)) from None


def _replay(receipt: AdministrationReceipt, request_hash: str) -> tuple[int, AdministrationResult]:
    if receipt.get("requestHash") != request_hash:
        raise AdministrationConflict("同じ Idempotency-Key に異なる内容は指定できません。")
    return 200, receipt["result"]
