"""自分宛ての通知と既読位置の API（GET /api/notifications・POST /api/notifications/read）。

宛先グループは業務ロールと所属チームから決める（設計書「通知」）:
Curator は Curator 全員宛て、Admin は Admin 全員宛て、Publisher は所属チームの Publisher 全員宛てを読む。
宛先グループを持たない Viewer は、通知が0件になる（エラーにはしない）。
補助ストアの失敗や保存内容の異常は0件に置き換えず、502 で区別する。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.auth import Caller, Role
from registry_portal.domain.notifications import (
    ADMIN_AUDIENCE,
    CURATOR_AUDIENCE,
    POSITION_PATTERN,
    team_audience,
)
from registry_portal.errors import ApiError, RequestValidationError
from registry_portal.operations import principal_of
from registry_portal.services.notification_store import NotificationStore

# 一覧に返す通知の上限（宛先グループをまたいで新しい順）。SPA の NOTIFICATION_LIST_LIMIT と揃える
NOTIFICATION_LIST_LIMIT = 50
# 既読位置の更新の本文の上限
READ_BODY_MAX_LENGTH = 1024


class NotificationsNotConfigured(ApiError):
    """補助テーブルが未設定で、通知を提供できない。"""

    status = 503
    code = "NOTIFICATIONS_NOT_CONFIGURED"
    default_message = "通知は準備中です。管理者に連絡してください。"


class NotificationsUnavailable(ApiError):
    """通知または既読位置を読み書きできなかった。0件とは区別する。"""

    status = 502
    code = "NOTIFICATIONS_UNAVAILABLE"
    default_message = "通知を取得できませんでした。時間をおいて再度お試しください。"


_STORED_INVALID = "保存された通知の形式が正しくありません。管理者に連絡してください。"


def audiences_of(caller: Caller) -> list[str]:
    """呼び出し元が属する宛先グループ。Publisher は所属チームごとの宛先グループに属する。"""
    audiences = []
    if Role.CURATOR in caller.roles:
        audiences.append(CURATOR_AUDIENCE)
    if Role.ADMIN in caller.roles:
        audiences.append(ADMIN_AUDIENCE)
    if Role.PUBLISHER in caller.roles:
        audiences.extend(team_audience(team) for team in caller.teams)
    return audiences


class NotificationInbox:
    """自分宛ての通知の一覧・未読件数と、既読位置の更新。"""

    def __init__(self, store: NotificationStore) -> None:
        self.store = store

    def list(self, caller: Caller, query: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        if query:
            raise RequestValidationError("通知の一覧にクエリパラメーターは指定できません。")
        return 200, self._inbox(caller)

    def mark_read(self, caller: Caller, body: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """既読位置を進める。指定した位置（一覧の通知の position）までの通知が既読になる。"""
        position = body.get("readUpTo")
        if set(body) != {"readUpTo"} or not isinstance(position, str):
            raise RequestValidationError(
                "readUpTo に既読にする通知の position を指定してください。"
            )
        if not POSITION_PATTERN.fullmatch(position):
            raise RequestValidationError("readUpTo の形式が正しくありません。")
        try:
            self.store.save_read_position(principal_of(caller), position)
        except (BotoCoreError, ClientError):
            raise NotificationsUnavailable() from None
        return 200, self._inbox(caller)

    def _inbox(self, caller: Caller) -> dict[str, Any]:
        audiences = audiences_of(caller)
        try:
            read_up_to = self.store.read_position(principal_of(caller))
            notifications = [
                notification
                for audience in audiences
                for notification in self.store.latest(audience, NOTIFICATION_LIST_LIMIT)
            ]
            unread = sum(self.store.count_after(audience, read_up_to) for audience in audiences)
        except (BotoCoreError, ClientError):
            raise NotificationsUnavailable() from None
        except ValueError:
            raise NotificationsUnavailable(_STORED_INVALID) from None
        notifications.sort(key=lambda notification: notification.position, reverse=True)
        return {
            "items": [
                notification.view(read_up_to)
                for notification in notifications[:NOTIFICATION_LIST_LIMIT]
            ],
            "unreadCount": unread,
            "readUpTo": read_up_to,
        }
