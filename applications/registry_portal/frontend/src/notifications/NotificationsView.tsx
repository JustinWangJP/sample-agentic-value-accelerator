import { useCallback, useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { NOTIFICATION_KIND_LABELS, NOTIFICATION_POLL_INTERVAL_MS, type NotificationInbox } from './api'

type Props = { api: ApiClient; onRelogin: () => void }

/**
 * ヘッダーの通知欄。表示している間は約60秒ごとに自分宛ての通知を取得し、未読件数を出す。
 * 一覧から既読にできる（既読はユーザー単位の既読位置で、最新の通知の位置まで進める）
 */
export function NotificationsView({ api, onRelogin }: Props) {
  const [inbox, setInbox] = useState<NotificationInbox | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [open, setOpen] = useState(false)
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

  const label =
    error !== null
      ? '通知（未読件数を取得できません）'
      : inbox === null
        ? '通知（取得中）'
        : `通知（未読 ${inbox.unreadCount} 件）`

  return (
    <section aria-label="通知" className="notifications">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        {label}
      </button>
      {open && (
        <div>
          {error !== null ? (
            <RequestErrorAlert error={error} onRetry={() => void load()} onRelogin={onRelogin} />
          ) : inbox === null ? (
            <p role="status">通知を取得しています…</p>
          ) : inbox.items.length === 0 ? (
            <p>通知はありません。</p>
          ) : (
            <>
              {inbox.unreadCount > 0 && (
                <button type="button" disabled={busy} onClick={() => void markAllRead(inbox)}>
                  すべて既読にする
                </button>
              )}
              <ul>
                {inbox.items.map((item) => (
                  <li key={item.position}>
                    {!item.read && <strong>未読 </strong>}
                    <span>{NOTIFICATION_KIND_LABELS[item.kind]}</span>
                    {' '}
                    <span>{item.recordName ?? item.recordId}</span>
                    <p>{item.message}</p>
                    <time dateTime={item.createdAt}>{item.createdAt}</time>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  )
}
