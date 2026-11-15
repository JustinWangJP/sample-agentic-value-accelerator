"""Registry の EventBridge イベントから通知を作るイベント処理（#15）。

イベントに含まれるのは record ID だけで、操作者は含まれない。そのため Registry レコードを再取得し、
補助テーブルの操作記録と照合して「ポータル内の変更か、外の変更か」を判定する（設計書「通知」）。
EventBridge は重複して配信したり順序が入れ替わったりするので、event ID で重複を除き、
状態は常に再取得した値を使う。同じ状態変化（レコード・状態・AWS の updatedAt）からは、
別の event ID で届いても宛先グループ・種別ごとに1件しか通知を作らない。

このハンドラーが行うのは、Registry の読み取りと、補助テーブルへの通知の書き込みだけである。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, NamedTuple

from botocore.exceptions import ClientError

from registry_portal.curation_reads import RegistryReadClient, is_managed
from registry_portal.domain.catalog import display_name, related_reference_ids, snapshot_label
from registry_portal.domain.drafts import (
    APPROVE_KIND,
    AUDIT_RESULT,
    DEPRECATE_KIND,
    DEPRECATED_STATUS,
    OPERATION_KINDS,
    PENDING_APPROVAL_STATUS,
    RECORD_ID_PATTERN,
    REJECT_KIND,
    REJECTED_STATUS,
    SUSPEND_KIND,
    Management,
    OperationKind,
)
from registry_portal.domain.notifications import (
    ADMIN_AUDIENCE,
    CURATOR_AUDIENCE,
    NOTIFIED_STATUSES,
    Audience,
    BrokenSendTime,
    Notification,
    NotificationKind,
    ObservedOperation,
    explaining_operation,
    team_audience,
    transition_key,
)
from registry_portal.services.notification_store import NotificationStore
from registry_portal.services.reference_index import ReferenceIndex
from registry_portal.services.supplement_store import AppendedRecord, SupplementStore

logger = logging.getLogger(__name__)

EVENT_SOURCE = "aws.agent-registry"
# Registry レコードのイベントの detail-type（[Registry EventBridge イベント] の表）
RECORD_EVENT_PREFIX = "Registry Record State changed to "
RECORD_EVENT_STATES = ("Draft", "Pending Approval", "Approved", "Rejected", "Deprecated")


# 照合した操作の種別ごとの、所有チームへの通知の種別と、参照先を使う Agent の所有チームへの通知の種別
_OWNER_NOTIFICATIONS: Mapping[OperationKind, tuple[NotificationKind, NotificationKind | None]] = {
    APPROVE_KIND: ("APPROVED", None),
    REJECT_KIND: ("REJECTED", None),
    SUSPEND_KIND: ("SUSPENDED", "REFERENCE_SUSPENDED"),
    DEPRECATE_KIND: ("DEPRECATED", "REFERENCE_DEPRECATED"),
}

# ポータル外の変更のうち、参照先を使う Agent の所有チームにも知らせる状態と、その通知の種別。
# 公開停止（REJECTED）も廃止も、参照先が発見できなくなる点はポータル内の操作と変わらないため。
# ポータル外の REJECTED は公開停止と却下を区別できないが、参照先を使う Agent があるのは
# 公開承認された版を持つ参照先だけなので、公開停止として知らせる
_OUTSIDE_REFERENCE_NOTIFICATIONS: Mapping[str, NotificationKind] = {
    REJECTED_STATUS: "REFERENCE_SUSPENDED",
    DEPRECATED_STATUS: "REFERENCE_DEPRECATED",
}


class _Delivery(NamedTuple):
    """宛先グループ1つに届ける通知の種別。参照先の通知では、対象の Agent の表示名も持つ。"""

    audience: Audience
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
        references: ReferenceIndex,
        client: RegistryReadClient,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.registry_arn = registry_arn
        self.registry_id = registry_arn.rsplit("/", 1)[1]
        self.store = store
        self.notifications = notifications
        self.references = references
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

    def _last_sent_at(self, operation: ObservedOperation) -> datetime | None:
        """操作の実行制御から、Registry への送信時刻を読む（#38）。

        送信時刻を記録する前の操作と、確定から保持期間（7日）が過ぎて実行制御が消えた操作は None
        （操作記録の開始・確定時刻で照合する）。記録した送信時刻の型・形式が崩れていれば BrokenSendTime
        （その操作は照合に使わない）。補助テーブルの一時的な失敗は例外のまま返し、再試行させる。
        """
        control = self.store.get_operation(operation.operation_id)
        if control is None or "lastSentAt" not in control:
            return None
        sent_at = _parse_time(control["lastSentAt"])
        if sent_at is None:
            logger.warning(
                "送信時刻の形式が正しくありません: operation_id=%s", operation.operation_id
            )
            raise BrokenSendTime(operation.operation_id)
        return sent_at

    def _deliveries(
        self, record_id: str, status: str, updated_at: datetime, management: Management | None
    ) -> list[_Delivery]:
        operation = explaining_operation(
            observed_operations(self.store.list_appended_records(record_id)),
            status,
            updated_at,
            self._last_sent_at,
        )
        if operation is None:
            deliveries = [_Delivery(ADMIN_AUDIENCE, "OUTSIDE_CHANGE")]
            # 管理下のレコードは、所有チームにも自分のレコードの変化を知らせる。通常の決裁と同じ種別にはせず、
            # Curator にも届けない（ポータル外の審査待ちは、そのまま決裁せず保存・再申請から進める。
            # 設計書「通知」）。未管理レコードにはポータル上の所有チームがないため、Admin にだけ届ける
            if is_managed(management):
                deliveries.append(_Delivery(team_audience(management.owner_team), "OUTSIDE_CHANGE"))
            reference_kind = _OUTSIDE_REFERENCE_NOTIFICATIONS.get(status)
            if reference_kind is not None:
                deliveries.extend(self._reference_deliveries(record_id, reference_kind))
            return deliveries
        if status == PENDING_APPROVAL_STATUS:
            return [_Delivery(CURATOR_AUDIENCE, "SUBMITTED")]
        owner = management.owner_team if is_managed(management) else operation.owner_team
        # 照合した操作の種別から、所有チームへの通知と参照先を使う Agent への通知の種別を決める。
        # 却下と公開停止はどちらも REJECTED なので、状態ではなく操作の種別で区別する。
        # ポータル内の作成・編集による下書き（DRAFT）は通知しない
        kinds = _OWNER_NOTIFICATIONS.get(operation.kind)
        if kinds is None:
            return []
        owner_kind, reference_kind = kinds
        deliveries = [_Delivery(team_audience(owner), owner_kind)] if owner else []
        if reference_kind is not None:
            deliveries.extend(self._reference_deliveries(record_id, reference_kind))
        return deliveries

    def _reference_deliveries(self, target_id: str, kind: NotificationKind) -> list[_Delivery]:
        """参照先（target_id）を公開中の版で使う Agent の所有チームごとの通知。

        所有チームや公開中の版の関連参照を読めない Agent は、黙って飛ばさず Admin に知らせる
        （CODING_STANDARDS「保存内容の異常を空の結果に変換しない」）。逆引きは公開承認でしか書かないため、
        逆引きにある Agent は管理下で、管理情報と公開版スナップショットを持つはずである。
        """
        agents_by_team: dict[str, list[str]] = {}
        unreadable: list[str] = []
        for agent_id in self.references.referencing_records(target_id):
            try:
                team, label = self._referencing_agent(agent_id, target_id)
            except ValueError:
                logger.warning(
                    "参照先を使う Agent の保存内容を読めません: agent_id=%s target_id=%s",
                    agent_id,
                    target_id,
                )
                unreadable.append(agent_id)
                continue
            if team is not None:
                agents_by_team.setdefault(team, []).append(label)
        deliveries = [
            _Delivery(team_audience(team), kind, tuple(sorted(agents)))
            for team, agents in sorted(agents_by_team.items())
        ]
        if unreadable:
            deliveries.append(_Delivery(ADMIN_AUDIENCE, kind, tuple(sorted(unreadable))))
        return deliveries

    def _referencing_agent(self, agent_id: str, target_id: str) -> tuple[str | None, str]:
        """逆引きにある Agent の所有チームと表示名。公開中の版が参照先を使っていなければ所有チームは None。

        管理情報・公開版スナップショットがない、または形式が崩れていれば ValueError。
        """
        management = self.store.get_management(agent_id)
        if not is_managed(management):
            raise ValueError("逆引きにある Agent の管理情報がありません。")
        version = management.recorded_published_version
        if version is None:
            # 廃止した Agent には公開中の版がない（逆引きの項目は消さずに残る）
            return None, agent_id
        snapshot = self.store.get_published(agent_id, version)
        if snapshot is None:
            raise ValueError("公開中の版の公開版スナップショットがありません。")
        if target_id not in related_reference_ids(snapshot):
            # 新しい版の公開で参照をやめた Agent は対象外（逆引きの項目は消さずに残る）
            return None, agent_id
        return management.owner_team, snapshot_label(agent_id, snapshot)

    def _notify(self, event_id: str, record_id: str, record: Mapping[str, Any]) -> int:
        status = str(record.get("status"))
        if status not in NOTIFIED_STATUSES:
            # 処理中・失敗の状態は遷移が確定していない。確定した後の遷移のイベントで通知する
            return 0
        updated_at = _aware(record["updatedAt"])
        management = self._management(record_id)
        deliveries = self._deliveries(record_id, status, updated_at, management)
        # 同じ状態変化は、別の event ID で届いても（順序の入れ替わりで再取得の結果が同じでも）1回だけ通知する
        transition = transition_key(record_id, status, updated_at)
        name = display_name(record)
        reason = record.get("statusReason")
        created_at = self.clock()
        published = 0
        for delivery in deliveries:
            notification = Notification.create(
                transition,
                delivery.audience,
                delivery.kind,
                record_id,
                name,
                created_at,
                reason=reason if isinstance(reason, str) else None,
                status=status,
                agents=delivery.agents,
                event_id=event_id,
            )
            if self.notifications.publish(notification, transition):
                published += 1
        return published
