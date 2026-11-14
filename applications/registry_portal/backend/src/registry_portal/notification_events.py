"""Registry の EventBridge イベントから通知を作るイベント処理（#15）。

イベントに含まれるのは record ID だけで、操作者は含まれない。そのため Registry レコードを再取得し、
補助テーブルの操作記録と照合して「ポータル内の変更か、外の変更か」を判定する（設計書「通知」）。
EventBridge は重複して配信したり順序が入れ替わったりするので、event ID で重複を除き、
状態は常に再取得した値を使う。同じ状態変化（レコード・状態・AWS の updatedAt）からは、
別の event ID で届いても宛先・種別ごとに1件しか通知を作らない。

このハンドラーが行うのは、Registry の読み取りと、補助テーブルへの通知の書き込みだけである。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, NamedTuple

from botocore.exceptions import ClientError

from registry_portal.curation_reads import RegistryReadClient, display_name
from registry_portal.domain.drafts import (
    AUDIT_RESULT,
    DEPRECATED_STATUS,
    OPERATION_KINDS,
    PENDING_APPROVAL_STATUS,
    PUBLISHED_STATUS,
    RECORD_ID_PATTERN,
    REJECTED_STATUS,
    SUSPEND_KIND,
    Management,
)
from registry_portal.domain.notifications import (
    ADMIN_AUDIENCE,
    CURATOR_AUDIENCE,
    NOTIFIED_STATUSES,
    Notification,
    NotificationKind,
    ObservedOperation,
    explaining_operation,
    notification_id,
    notification_message,
    notification_time,
    team_audience,
)
from registry_portal.services.notification_store import NotificationStore
from registry_portal.services.supplement_store import AppendedRecord, SupplementStore

logger = logging.getLogger(__name__)

EVENT_SOURCE = "aws.agent-registry"
# Registry レコードのイベントの detail-type（[Registry EventBridge イベント] の表）
RECORD_EVENT_PREFIX = "Registry Record State changed to "
RECORD_EVENT_STATES = ("Draft", "Pending Approval", "Approved", "Rejected", "Deprecated")


class _Plan(NamedTuple):
    audience: str
    kind: NotificationKind
    agents: tuple[str, ...] = ()


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def observed_operations(records: list[AppendedRecord]) -> list[ObservedOperation]:
    """追記記録を操作ごとに重ね、照合に使う形にする。

    形式の崩れた操作記録は照合に使わない（説明できない変化として Admin に届く）。
    """
    merged: dict[str, dict[str, Any]] = {}
    phases: dict[str, set[str]] = {}
    for record in records:
        merged[record.operation_id] = {**merged.get(record.operation_id, {}), **record.item}
        phases.setdefault(record.operation_id, set()).add(record.phase)
    operations = []
    for op_id, item in merged.items():
        started_at = _parse_time(item.get("startedAt"))
        if item.get("kind") not in OPERATION_KINDS or started_at is None:
            continue
        settled = AUDIT_RESULT in phases[op_id]
        owner = item.get("ownerTeam")
        operations.append(
            ObservedOperation(
                operation_id=op_id,
                kind=item["kind"],
                started_at=started_at,
                settled_at=_parse_time(item.get("settledAt")) if settled else None,
                state=(
                    ("FAILED" if "errorCode" in item else "SUCCEEDED") if settled else "VERIFYING"
                ),
                owner_team=owner if isinstance(owner, str) else None,
            )
        )
    return operations


class RegistryEventNotifier:
    """Registry のレコードのイベント1件から、宛先グループごとの通知を作る。"""

    def __init__(
        self,
        registry_arn: str,
        store: SupplementStore,
        notifications: NotificationStore,
        client: RegistryReadClient,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.registry_arn = registry_arn
        self.registry_id = registry_arn.rsplit("/", 1)[1]
        self.store = store
        self.notifications = notifications
        self.client = client
        self.clock = clock

    def _parse(self, event: Mapping[str, Any]) -> tuple[str, str] | None:
        """対象のイベントなら event ID と record ID。別の Registry や想定外の形は None。"""
        detail = event.get("detail")
        event_id = event.get("id")
        detail_type = event.get("detail-type")
        if (
            event.get("source") != EVENT_SOURCE
            or not isinstance(event_id, str)
            or not event_id
            or not isinstance(detail_type, str)
            or detail_type.removeprefix(RECORD_EVENT_PREFIX) not in RECORD_EVENT_STATES
            or not detail_type.startswith(RECORD_EVENT_PREFIX)
            or not isinstance(detail, Mapping)
            or detail.get("registryId") not in (self.registry_id, self.registry_arn)
        ):
            return None
        record_id = detail.get("registryRecordId")
        if not isinstance(record_id, str) or not RECORD_ID_PATTERN.fullmatch(record_id):
            return None
        return event_id, record_id

    def handle(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """イベントを処理する。Registry・補助テーブルの一時的な失敗は例外のまま返し、再試行させる。"""
        parsed = self._parse(event)
        if parsed is None:
            logger.warning("対象外のイベントを無視しました: id=%s", event.get("id"))
            return {"result": "IGNORED", "notified": 0}
        event_id, record_id = parsed
        if self.notifications.event_processed(event_id):
            return {"result": "DUPLICATE", "notified": 0}
        try:
            record = self.client.get_registry_record(
                registryId=self.registry_arn, recordId=record_id
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
            # 削除されたレコードには通知先を決められない。再試行しても変わらないため処理済みにする
            logger.warning("イベントの Registry レコードがありません: id=%s", event_id)
            self.notifications.mark_event_processed(event_id)
            return {"result": "RECORD_NOT_FOUND", "notified": 0}
        notified = self._notify(event_id, record_id, record)
        self.notifications.mark_event_processed(event_id)
        logger.info(
            "イベントを処理しました: id=%s status=%s notified=%d",
            event_id,
            record.get("status"),
            notified,
        )
        return {"result": "PROCESSED", "notified": notified}

    def _management(self, record_id: str) -> Management | None:
        try:
            return self.store.get_management(record_id)
        except ValueError:
            # 管理情報の形式が崩れていれば所有チームを決められない。照合できない変化として扱う
            logger.warning("管理情報の形式が正しくありません: record_id=%s", record_id)
            return None

    def _plans(
        self, record_id: str, status: str, updated_at: datetime, management: Management | None
    ) -> list[_Plan]:
        operation = explaining_operation(
            observed_operations(self.store.list_appended_records(record_id)), status, updated_at
        )
        if operation is None:
            plans = [_Plan(ADMIN_AUDIENCE, "OUTSIDE_CHANGE")]
            # 廃止は元に戻せないため、ポータル外の廃止でも参照先を使う Agent の所有チームに知らせる
            if status == DEPRECATED_STATUS:
                plans.extend(self._reference_plans(record_id, "REFERENCE_DEPRECATED"))
            return plans
        owner = (
            management.owner_team
            if management is not None and management.is_managed
            else operation.owner_team
        )
        owner_plans: list[_Plan] = []
        if status == PENDING_APPROVAL_STATUS:
            return [_Plan(CURATOR_AUDIENCE, "SUBMITTED")]
        if status == PUBLISHED_STATUS:
            owner_plans = [_Plan(team_audience(owner), "APPROVED")] if owner else []
        elif status == REJECTED_STATUS and operation.kind == SUSPEND_KIND:
            owner_plans = [_Plan(team_audience(owner), "SUSPENDED")] if owner else []
            owner_plans.extend(self._reference_plans(record_id, "REFERENCE_SUSPENDED"))
        elif status == REJECTED_STATUS:
            owner_plans = [_Plan(team_audience(owner), "REJECTED")] if owner else []
        elif status == DEPRECATED_STATUS:
            owner_plans = [_Plan(team_audience(owner), "DEPRECATED")] if owner else []
            owner_plans.extend(self._reference_plans(record_id, "REFERENCE_DEPRECATED"))
        # 下書き（DRAFT）への変化は、ポータル内の作成・編集なら通知しない
        return owner_plans

    def _reference_plans(self, target_id: str, kind: NotificationKind) -> list[_Plan]:
        """参照先（target_id）を公開中の版で使う Agent の所有チームごとの通知。"""
        agents_by_team: dict[str, list[str]] = {}
        for agent_id in self.store.referencing_records(target_id):
            management = self._management(agent_id)
            if management is None or not management.is_managed:
                continue
            # 最後に公開承認した版（管理情報の publishedVersion）の関連参照を正とする
            version = management.item.get("publishedVersion")
            snapshot = (
                self.store.get_published(agent_id, version) if isinstance(version, str) else None
            )
            references = snapshot.get("relatedReferences") if snapshot else None
            if not isinstance(references, list) or not any(
                isinstance(reference, Mapping) and reference.get("recordId") == target_id
                for reference in references
            ):
                # 新しい版の公開で参照をやめた Agent は対象外（逆引きの項目は消さずに残る）
                continue
            agents_by_team.setdefault(management.owner_team, []).append(
                _agent_label(agent_id, snapshot)
            )
        return [
            _Plan(team_audience(team), kind, tuple(sorted(agents)))
            for team, agents in sorted(agents_by_team.items())
        ]

    def _notify(self, event_id: str, record_id: str, record: Mapping[str, Any]) -> int:
        status = str(record.get("status"))
        if status not in NOTIFIED_STATUSES:
            # 処理中・失敗の状態は遷移が確定していない。確定した後の遷移のイベントで通知する
            return 0
        updated_at = _aware(record["updatedAt"])
        management = self._management(record_id)
        plans = self._plans(record_id, status, updated_at, management)
        # 同じ状態変化は、別の event ID で届いても（順序の入れ替わりで再取得の結果が同じでも）1回だけ通知する
        transition = f"TRANSITION#{record_id}#{status}#{notification_time(updated_at)}"
        name = display_name(record)
        reason = record.get("statusReason")
        created_at = notification_time(self.clock())
        published = 0
        for plan in plans:
            notification = Notification(
                notification_id=notification_id(transition, plan.audience, plan.kind),
                audience=plan.audience,
                kind=plan.kind,
                record_id=record_id,
                record_name=name,
                message=notification_message(
                    plan.kind,
                    record_id,
                    name,
                    reason=reason if isinstance(reason, str) else None,
                    status=status,
                    agents=plan.agents,
                ),
                created_at=created_at,
                event_id=event_id,
            )
            if self.notifications.publish(notification, transition):
                published += 1
        return published


def _agent_label(agent_id: str, snapshot: Mapping[str, Any] | None) -> str:
    """公開版スナップショットの定義にある表示名。なければ record ID。"""
    record = snapshot.get("record") if snapshot else None
    if isinstance(record, Mapping):
        for key in ("displayName", "name"):
            if isinstance(record.get(key), str) and record[key]:
                return f"{record[key]}（{agent_id}）"
    return agent_id
