// 画面上の通知（Seam 2: SPA の fetch 境界）。自分宛ての通知と未読件数の取得、既読位置の更新。
// バックエンドの notifications.py・domain/notifications.py と契約を揃える。
import { hasStrings, isObject } from '../api/guards'
import { createResponseChecker, type ApiRequest } from '../api/request'

/** 通知の種別。バックエンドの NotificationKind（domain/notifications.py）と揃える */
export const NOTIFICATION_KINDS = [
  'SUBMITTED',
  'DEPRECATION_REQUESTED',
  'APPROVED',
  'REJECTED',
  'SUSPENDED',
  'DEPRECATED',
  'DEPRECATION_DECLINED',
  'REFERENCE_SUSPENDED',
  'REFERENCE_DEPRECATED',
  'OUTSIDE_CHANGE',
] as const
export type NotificationKind = (typeof NOTIFICATION_KINDS)[number]

export const NOTIFICATION_KIND_LABELS: Record<NotificationKind, string> = {
  SUBMITTED: '公開申請',
  DEPRECATION_REQUESTED: '廃止依頼',
  APPROVED: '公開承認',
  REJECTED: '却下',
  SUSPENDED: '公開停止',
  DEPRECATED: '廃止',
  DEPRECATION_DECLINED: '廃止依頼の見送り',
  REFERENCE_SUSPENDED: '参照先の公開停止',
  REFERENCE_DEPRECATED: '参照先の廃止',
  OUTSIDE_CHANGE: 'ポータル外の変更',
}

/** ポーリングの間隔（設計書「画面と機能の範囲」の通知欄: 約60秒ごと） */
export const NOTIFICATION_POLL_INTERVAL_MS = 60_000

export type Notification = {
  notificationId: string
  /** 既読位置と比べる値（時刻#通知 ID）。既読にするときはこの値を送る */
  position: string
  kind: NotificationKind
  recordId: string
  /** Registry を読まないアプリ内の通知（廃止依頼の見送り）では null */
  recordName: string | null
  message: string
  createdAt: string
  read: boolean
}

export type NotificationInbox = {
  /** 新しい順。宛先グループをまたいで最大50件 */
  items: Notification[]
  unreadCount: number
  /** 自分の既読位置。まだ既読にしていなければ null */
  readUpTo: string | null
}

export type NotificationApi = {
  listNotifications: () => Promise<NotificationInbox>
  markNotificationsRead: (readUpTo: string) => Promise<NotificationInbox>
}

function isNotification(value: unknown): value is Notification {
  return (
    isObject(value) &&
    hasStrings(value, ['notificationId', 'position', 'recordId', 'message', 'createdAt']) &&
    NOTIFICATION_KINDS.some((kind) => kind === value.kind) &&
    (value.recordName === null || typeof value.recordName === 'string') &&
    typeof value.read === 'boolean'
  )
}

function isInbox(value: unknown): value is NotificationInbox {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isNotification) &&
    Number.isInteger(value.unreadCount) &&
    (value.unreadCount as number) >= 0 &&
    (value.readUpTo === null || typeof value.readUpTo === 'string')
  )
}

export function createNotificationApi(request: ApiRequest, invalidResponse: () => Error): NotificationApi {
  const checked = createResponseChecker(invalidResponse)
  return {
    listNotifications: () => checked(request('GET', '/api/notifications'), isInbox),
    markNotificationsRead: (readUpTo) =>
      checked(request('POST', '/api/notifications/read', { readUpTo }), isInbox),
  }
}
