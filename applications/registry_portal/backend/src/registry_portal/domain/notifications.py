"""画面上の通知のモデルと、Registry の状態変化をポータルの操作記録と照合する規則（#15）。

通知は宛先グループ（Curator 全員・Admin 全員・所有チームの Publisher 全員）ごとに1件だけ作り、
既読はユーザー単位の既読位置で管理する（設計書「通知」）。保持期間は90日で、補助テーブルの TTL で消す。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, NamedTuple, get_args

from registry_portal.domain.drafts import (
    APPROVE_KIND,
    CREATE_KIND,
    DEPRECATE_KIND,
    DEPRECATED_STATUS,
    DRAFT_STATUS,
    PENDING_APPROVAL_STATUS,
    PUBLISHED_STATUS,
    REJECT_KIND,
    REJECTED_STATUS,
    SUBMIT_KIND,
    SUSPEND_KIND,
    UPDATE_KIND,
    OperationKind,
    digest,
)

# 通知の種別。SPA の `NOTIFICATION_KINDS`（frontend/src/notifications/api.ts）と揃える
NotificationKind = Literal[
    # 申請（AWS の PENDING_APPROVAL）。Curator 全員へ
    "SUBMITTED",
    # 廃止依頼（アプリ内で発生）。Curator 全員へ
    "DEPRECATION_REQUESTED",
    # 公開承認・却下・公開停止・廃止（AWS の状態変化）。所有チームの Publisher 全員へ
    "APPROVED",
    "REJECTED",
    "SUSPENDED",
    "DEPRECATED",
    # 廃止依頼の見送り（アプリ内で発生。AWS の状態を変えないためイベントは出ない）。所有チームの Publisher 全員へ
    "DEPRECATION_DECLINED",
    # 参照先の Skill・MCP サーバーの公開停止・廃止。その参照先を使う Agent の所有チームへ
    "REFERENCE_SUSPENDED",
    "REFERENCE_DEPRECATED",
    # 操作記録と照合できない状態変化（ポータル外の変更）。Admin 全員へ
    "OUTSIDE_CHANGE",
]
NOTIFICATION_KINDS: tuple[NotificationKind, ...] = get_args(NotificationKind)

# 通知と既読位置の保持期間（設計書「補助テーブルに保存するもの」）。TTL の属性に書く
NOTIFICATION_RETENTION_SECONDS = 90 * 24 * 60 * 60

# 宛先グループ。Curator 全員・Admin 全員・所有チーム（team-*）の Publisher 全員の3種類
CURATOR_AUDIENCE = "CURATOR"
ADMIN_AUDIENCE = "ADMIN"
_TEAM_AUDIENCE_PREFIX = "TEAM#"


def team_audience(team: str) -> str:
    """所有チームの Publisher 全員を表す宛先グループ。"""
    return f"{_TEAM_AUDIENCE_PREFIX}{team}"


def _is_audience(value: object) -> bool:
    return isinstance(value, str) and (
        value in (CURATOR_AUDIENCE, ADMIN_AUDIENCE)
        or (value.startswith(_TEAM_AUDIENCE_PREFIX) and len(value) > len(_TEAM_AUDIENCE_PREFIX))
    )


# 通知の時刻の形式。既読位置（時刻#通知 ID）を文字列の大小で比べるため、桁を固定する
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
# 既読位置の形式。SPA から受け取る値もこの形式に限る
POSITION_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z#[0-9a-f]{64}")


def notification_time(value: datetime | str) -> str:
    """通知の時刻（UTC・マイクロ秒までの固定桁）。ISO 8601 の文字列も受け取る。"""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def transition_key(record_id: str, status: str, updated_at: datetime) -> str:
    """Registry の状態変化（レコード・状態・AWS の updatedAt）を表す出来事のキー。

    別の event ID で届いても、同じ状態変化からは宛先・種別ごとに1件しか通知を作らない。
    """
    return f"TRANSITION#{record_id}#{status}#{notification_time(updated_at)}"


def operation_key(operation_id: str) -> str:
    """アプリ内の操作（廃止依頼・見送り）を表す出来事のキー。"""
    return f"OPERATION#{operation_id}"


def notification_id(dedup_key: str, audience: str, kind: NotificationKind) -> str:
    """同じ出来事・宛先・種別には同じ ID を割り当てる（再処理で別の通知を作らない）。"""
    return digest(["NOTIFICATION", dedup_key, audience, kind])


_RECORD_LABEL_MAX = 256


@dataclass(frozen=True)
class Notification:
    """宛先グループ1つへの通知1件。"""

    notification_id: str
    audience: str
    kind: NotificationKind
    record_id: str
    # 表示名（AWS の displayName、なければ name）。Registry を読まないアプリ内の通知では None
    record_name: str | None
    message: str
    created_at: str
    # EventBridge のイベントから作った通知の event ID（設計書「補助テーブルに保存するもの」）
    event_id: str | None = None

    @property
    def position(self) -> str:
        """既読位置と比べる値。時刻の順に並び、同じ時刻なら通知 ID で決まる。"""
        return f"{self.created_at}#{self.notification_id}"

    @classmethod
    def create(
        cls,
        dedup_key: str,
        audience: str,
        kind: NotificationKind,
        record_id: str,
        record_name: str | None,
        created_at: datetime | str,
        *,
        reason: str | None = None,
        status: str | None = None,
        agents: Iterable[str] = (),
        event_id: str | None = None,
    ) -> Notification:
        """出来事（dedup_key）・宛先・種別から通知を作る。ID・本文・時刻の形式はここだけで決める。

        dedup_key は、Registry のイベントなら状態変化（transition_key）、アプリ内の通知なら操作（operation_key）。
        """
        return cls(
            notification_id=notification_id(dedup_key, audience, kind),
            audience=audience,
            kind=kind,
            record_id=record_id,
            record_name=record_name,
            message=notification_message(
                kind,
                record_id,
                record_name,
                reason=reason,
                status=status,
                agents=agents,
                audience=audience,
            ),
            created_at=notification_time(created_at),
            event_id=event_id,
        )

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "notificationId": self.notification_id,
            "audience": self.audience,
            "kind": self.kind,
            "recordId": self.record_id,
            "message": self.message,
            "createdAt": self.created_at,
        }
        if self.record_name is not None:
            item["recordName"] = self.record_name
        if self.event_id is not None:
            item["eventId"] = self.event_id
        return item

    @classmethod
    def parse(cls, item: Mapping[str, Any]) -> Notification:
        """保存された通知を読む。型が契約と異なれば ValueError（空の結果に置き換えない）。"""
        record_name = item.get("recordName")
        event_id = item.get("eventId")
        if (
            not isinstance(item.get("notificationId"), str)
            or not _is_audience(item.get("audience"))
            or item.get("kind") not in NOTIFICATION_KINDS
            or not isinstance(item.get("recordId"), str)
            or not isinstance(item.get("message"), str)
            or not isinstance(item.get("createdAt"), str)
            or (record_name is not None and not isinstance(record_name, str))
            or (event_id is not None and not isinstance(event_id, str))
        ):
            raise ValueError("保存された通知の形式が正しくありません。")
        notification = cls(
            notification_id=item["notificationId"],
            audience=item["audience"],
            kind=item["kind"],
            record_id=item["recordId"],
            record_name=record_name,
            message=item["message"],
            created_at=item["createdAt"],
            event_id=event_id,
        )
        if not POSITION_PATTERN.fullmatch(notification.position):
            raise ValueError("保存された通知の形式が正しくありません。")
        return notification

    def view(self, read_up_to: str | None) -> dict[str, Any]:
        """API が返す形。SPA の isNotification（frontend/src/notifications/api.ts）と揃える。"""
        return {
            "notificationId": self.notification_id,
            "position": self.position,
            "kind": self.kind,
            "recordId": self.record_id,
            "recordName": self.record_name,
            "message": self.message,
            "createdAt": self.created_at,
            "read": read_up_to is not None and self.position <= read_up_to,
        }


def _label(record_name: str | None, record_id: str) -> str:
    return (record_name or record_id)[:_RECORD_LABEL_MAX]


def _with_reason(text: str, reason: str | None) -> str:
    return f"{text}理由: {reason}" if reason else text


def notification_message(
    kind: NotificationKind,
    record_id: str,
    record_name: str | None,
    *,
    reason: str | None = None,
    status: str | None = None,
    agents: Iterable[str] = (),
    audience: str = ADMIN_AUDIENCE,
) -> str:
    """通知の本文（日本語）。理由は Registry の statusReason または操作記録の理由。

    ポータル外の変更は、変更経路を調べる Admin と、経緯を Admin に確かめる所有チームとで文面を分ける。
    """
    label = f"「{_label(record_name, record_id)}」"
    agent_list = "、".join(agents)
    texts: dict[NotificationKind, str] = {
        "SUBMITTED": f"{label}の公開申請が届きました。審査待ちから確認してください。",
        "DEPRECATION_REQUESTED": _with_reason(
            f"{label}の廃止依頼が届きました。審査待ちから対応してください。", reason
        ),
        "APPROVED": f"{label}が公開承認されました。",
        "REJECTED": _with_reason(f"{label}の申請が却下されました。", reason),
        "SUSPENDED": _with_reason(f"{label}が公開停止されました。", reason),
        "DEPRECATED": _with_reason(f"{label}が廃止されました。", reason),
        "DEPRECATION_DECLINED": _with_reason(f"{label}の廃止依頼が見送られました。", reason),
        "REFERENCE_SUSPENDED": (
            f"参照先の{label}が公開停止されました。"
            f"この参照先を使う Agent（{agent_list}）を確認してください。"
        ),
        "REFERENCE_DEPRECATED": (
            f"参照先の{label}が廃止されました。"
            f"この参照先を使う Agent（{agent_list}）を確認してください。"
        ),
        "OUTSIDE_CHANGE": (
            f"{label}の状態がポータルの外で {status} に変わりました。"
            + (
                "操作記録と照合できないため、Registry の変更経路を確認してください。"
                if audience == ADMIN_AUDIENCE
                else "ポータルの操作ではないため、経緯は Admin に確認してください。"
            )
        ),
    }
    return texts[kind]


# ---- Registry の状態変化と操作記録の照合 ----

# AWS の状態を変えるポータルの操作と、その操作が成功したときの AWS の状態。
# 廃止依頼・見送り・取り込み・所有変更は AWS の状態を変えないため、照合の対象にしない
_STATUS_OPERATIONS: Mapping[str, frozenset[OperationKind]] = {
    DRAFT_STATUS: frozenset({CREATE_KIND, UPDATE_KIND}),
    PENDING_APPROVAL_STATUS: frozenset({SUBMIT_KIND}),
    PUBLISHED_STATUS: frozenset({APPROVE_KIND}),
    REJECTED_STATUS: frozenset({REJECT_KIND, SUSPEND_KIND}),
    DEPRECATED_STATUS: frozenset({DEPRECATE_KIND}),
}
STATE_CHANGING_KINDS: frozenset[OperationKind] = frozenset().union(*_STATUS_OPERATIONS.values())
# 通知の対象にする AWS の状態（Registry のイベントが出る状態）。処理中・失敗の状態は対象にしない
NOTIFIED_STATUSES: frozenset[str] = frozenset(_STATUS_OPERATIONS)

# Lambda と AWS の時計のずれの許容幅
CLOCK_SKEW = timedelta(minutes=5)
# Lambda の実行時間の上限。Registry への送信は、送信時刻からこの時間までに AWS に届くか、届かずに終わる
LAMBDA_MAX_DURATION = timedelta(minutes=15)

ObservedState = Literal["SUCCEEDED", "FAILED", "VERIFYING"]


class ObservedOperation(NamedTuple):
    """照合に使う操作記録1件（INTENT と RESULT を重ねたもの）。"""

    operation_id: str
    kind: OperationKind
    started_at: datetime
    # RESULT がない（結果確認中）か、確定時刻を記録する前に確定した操作は None
    settled_at: datetime | None
    state: ObservedState
    owner_team: str | None


# 操作の Registry への送信時刻（実行制御の lastSentAt）を引く。記録がなければ None
SentAtLookup = Callable[[ObservedOperation], datetime | None]


def explaining_operation(
    operations: Iterable[ObservedOperation],
    status: str,
    updated_at: datetime,
    sent_at: SentAtLookup,
) -> ObservedOperation | None:
    """観測した AWS の状態（status・updatedAt）を説明するポータルの操作。なければ None（ポータル外の変更）。

    状態の変化より前に始まった、AWS の状態を変える操作のうち最も新しいものが、
    - その状態に遷移させる種別であり
    - 失敗しておらず
    - Registry への送信時刻の前後（時計のずれと Lambda の上限）に変化が起きている
    ときだけ、ポータル内の変更とみなす。結果確認中の操作も、回復操作で確定した操作も、送信時刻で区切る。
    過去に同じ種別の操作があっても、その後に別の操作やポータル外の変更が挟まった状態変化は説明できない。

    sent_at は、最も新しい操作についてだけ呼ぶ（実行制御の読み取りを1回に抑える）。
    """
    candidates = [
        operation
        for operation in operations
        if operation.kind in STATE_CHANGING_KINDS
        and operation.started_at <= updated_at + CLOCK_SKEW
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda operation: operation.started_at)
    if latest.kind not in _STATUS_OPERATIONS.get(status, frozenset()):
        return None
    if latest.state == "FAILED":
        return None
    sent = sent_at(latest)
    if sent is not None:
        if sent - CLOCK_SKEW <= updated_at <= sent + LAMBDA_MAX_DURATION + CLOCK_SKEW:
            return latest
        return None
    # 送信時刻を記録する前の操作（と、確定から保持期間が過ぎて実行制御が消えた操作）は、開始から確定まで。
    # 確定時刻がなければ（結果確認中か、確定時刻を記録する前に確定した操作）開始から Lambda の上限まで
    settled = latest.settled_at or latest.started_at + LAMBDA_MAX_DURATION
    if updated_at > settled + CLOCK_SKEW:
        return None
    return latest
