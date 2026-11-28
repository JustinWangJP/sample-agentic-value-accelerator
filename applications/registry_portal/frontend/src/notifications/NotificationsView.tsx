import { useCallback, useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState } from '../components/StatePanel'
import { usePopover } from '../components/usePopover'
import { badge, popover } from '../ui/classes'
import {
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_POLL_INTERVAL_MS,
  type NotificationInbox,
  type NotificationKind,
} from './api'

type Props = { api: ApiClient; onRelogin: () => void }

/** 通知種別のバッジの色。依頼・申請は対応待ち、公開承認は完了、公開停止・廃止・却下は注意 */
const KIND_BADGE: Record<NotificationKind, string> = {
  SUBMITTED: badge.warning,
  DEPRECATION_REQUESTED: badge.warning,
  APPROVED: badge.success,
  REJECTED: badge.danger,
  SUSPENDED: badge.danger,
  DEPRECATED: badge.danger,
  DEPRECATION_DECLINED: badge.neutral,
  REFERENCE_SUSPENDED: badge.danger,
  REFERENCE_DEPRECATED: badge.danger,
  OUTSIDE_CHANGE: badge.danger,
}

/**
 * ヘッダーの通知欄。表示している間は約60秒ごとに自分宛ての通知を取得し、未読件数を出す。
 * 一覧から既読にできる（既読はユーザー単位の既読位置で、最新の通知の位置まで進める）
 */
export function NotificationsView({ api, onRelogin }: Props) {
  const [inbox, setInbox] = useState<NotificationInbox | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const { open, setOpen, container } = usePopover()
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const loaded = await api.listNotifications()
      setInbox(loaded)
      setError(null)
    } catch (failure) {
      // 取得の失敗を0件に置き換えない（前回の一覧も古い可能性があるため出さない）
      setInbox(null)
      setError(toApiError(failure, '通知を取得できませんでした。'))
    }
  }, [api])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), NOTIFICATION_POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [load])

  async function markAllRead(current: NotificationInbox) {
    const newest = current.items.reduce(
      (latest, item) => (item.position > latest ? item.position : latest),
      '',
    )
    setBusy(true)
    try {
      setInbox(await api.markNotificationsRead(newest))
      setError(null)
    } catch (failure) {
      setError(toApiError(failure, '既読にできませんでした。'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label="通知" className="relative" ref={container}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className={popover.trigger}>
        <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0a3 3 0 1 1-6 0" />
        </svg>
        {/* ボタンの名前は「通知（未読 N 件）」。件数だけをバッジで見せる */}
        {inbox === null && error === null ? (
          <span>通知（取得中）</span>
        ) : (
          <span>通知</span>
        )}
        {error !== null ? (
          <>
            <span className="sr-only">（未読件数を取得できません）</span>
            <span className="w-2.5 h-2.5 rounded-full bg-rose-500" aria-hidden="true" />
          </>
        ) : inbox === null ? null : (
          <>
            <span className="sr-only">（未読 </span>
            <span
              className={`inline-flex items-center justify-center min-w-5 h-5 px-1.5 text-xs font-semibold rounded-full ${
                inbox.unreadCount > 0 ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600'
              }`}
            >
              {inbox.unreadCount}
            </span>
            <span className="sr-only"> 件）</span>
          </>
        )}
      </button>
      {open && (
        <div className={popover.panel}>
          <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">通知</h2>
              <p className="text-xs text-slate-500">約60秒ごとに自動更新（90日間保持）</p>
            </div>
            {error === null && inbox !== null && inbox.unreadCount > 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void markAllRead(inbox)}
                className="text-xs font-medium text-blue-600 hover:text-blue-700 disabled:opacity-50"
              >
                すべて既読にする
              </button>
            )}
          </div>
          {error !== null ? (
            <div className="p-3">
              <RequestErrorAlert error={error} onRetry={() => void load()} onRelogin={onRelogin} />
            </div>
          ) : inbox === null ? (
            <div className="p-3">
              <LoadingState>通知を取得しています…</LoadingState>
            </div>
          ) : inbox.items.length === 0 ? (
            <div className="p-3">
              <EmptyState>通知はありません。</EmptyState>
            </div>
          ) : (
            <ul className="divide-y divide-slate-100 max-h-80 overflow-y-auto">
              {inbox.items.map((item) => (
                <li key={item.position} className={item.read ? 'px-4 py-3' : 'px-4 py-3 bg-blue-50/50'}>
                  <div className="flex items-start gap-2.5">
                    <span
                      className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${item.read ? 'bg-transparent' : 'bg-blue-600'}`}
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        {!item.read && <strong className="sr-only">未読 </strong>}
                        <span className={KIND_BADGE[item.kind]}>
                          {NOTIFICATION_KIND_LABELS[item.kind]}
                        </span>{' '}
                        <time dateTime={item.createdAt} className="text-xs text-slate-500">
                          {item.createdAt}
                        </time>
                      </div>
                      <p className="text-sm font-medium text-slate-900 mt-1 break-all">
                        {item.recordName ?? item.recordId}
                      </p>
                      <p className="text-xs text-slate-600 mt-0.5">{item.message}</p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="px-4 py-2.5 bg-slate-50 border-t border-slate-200 text-xs text-slate-500">最大 50 件を表示</div>
        </div>
      )}
    </section>
  )
}
