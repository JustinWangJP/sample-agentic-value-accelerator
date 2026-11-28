import type { ReactNode } from 'react'
import { button } from '../ui/classes'

type Props = {
  /** ApiError のほか、ページ送りの失敗などメッセージだけを持つ値も受け付ける */
  error: { message: string; requiresLogin: boolean; status?: number; code?: string; requestId?: string | null }
  /** 省略すると再試行ボタンを出さない（再試行しても解決しないエラー） */
  onRetry?: () => void
  onRelogin: () => void
  /** エラーに応じた追加の操作 */
  children?: ReactNode
}

/** HTTP の状態とエラーコード。サーバーから応答がない失敗（status 0）は出さない */
function statusLabel({ status, code }: Props['error']): string | null {
  if (!status || !code) return null
  return `失敗（${status} ${code}）`
}

/**
 * API の失敗を、0件とは別の状態として表示する。401 のときは再試行ではなくログインし直しを促し、
 * 403 など再試行しても解決しない失敗では再試行ボタンを出さない
 */
export function RequestErrorAlert({ error, onRetry, onRelogin, children }: Props) {
  const label = statusLabel(error)
  return (
    <div role="alert" className="bg-rose-50 border border-rose-200 rounded-xl p-4 space-y-2">
      {label && (
        <span className="inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold font-mono bg-rose-100 text-rose-800">
          {label}
        </span>
      )}
      <p className="text-sm font-semibold text-rose-950">{error.message}</p>
      {error.requestId && <p className="text-xs font-mono text-rose-700">requestId: {error.requestId}</p>}
      {(error.requiresLogin || onRetry || children) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {error.requiresLogin ? (
            <button type="button" className={button.danger} onClick={onRelogin}>
              もう一度ログインする
            </button>
          ) : (
            onRetry && (
              <button type="button" className={button.dangerOutline} onClick={onRetry}>
                再試行
              </button>
            )
          )}
          {children}
        </div>
      )}
    </div>
  )
}
