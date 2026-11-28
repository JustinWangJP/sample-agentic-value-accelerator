import type { ReactNode } from 'react'
import { button, statePanel } from '../ui/classes'
import { requestErrorKind, type DisplayedError } from './requestErrorKind'
import { StatePanelFrame } from './StatePanel'

type Props = {
  error: DisplayedError
  /** 再試行の操作。省略したとき、および 403・準備中（再試行しても解決しない）のときは再試行ボタンを出さない */
  onRetry?: () => void
  /** 再試行ボタンの文言 */
  retryLabel?: string
  onRelogin: () => void
  /** エラーに応じた追加の操作 */
  children?: ReactNode
}

/** HTTP の状態とエラーコード。サーバーから応答がない失敗（status 0）は出さない */
function statusText({ status, code }: DisplayedError): string | null {
  return status && code ? `${status} ${code}` : null
}

/**
 * API の失敗を、0件とは別の状態として表示する。準備中（503）は失敗と色とバッジで区別する。
 * 401 のときは再試行ではなくログインし直しを促し、403 と準備中では再試行ボタンを出さない
 */
export function RequestErrorAlert({ error, onRetry, retryLabel = '再試行', onRelogin, children }: Props) {
  const kind = requestErrorKind(error)
  const status = statusText(error)
  const preparing = kind === 'preparing'
  const tone = preparing ? 'warning' : 'danger'
  const retry = kind === 'failure' ? onRetry : undefined
  return (
    <StatePanelFrame tone={tone} badge={status && `${preparing ? '準備中' : '失敗'}（${status}）`} role="alert">
      <p className={`text-sm font-semibold ${statePanel[tone].title}`}>{error.message}</p>
      {error.requestId && <p className={`text-xs font-mono ${statePanel[tone].detail}`}>requestId: {error.requestId}</p>}
      {(kind === 'login' || retry || children) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {kind === 'login' ? (
            <button type="button" className={button.danger} onClick={onRelogin}>
              もう一度ログインする
            </button>
          ) : (
            retry && (
              <button type="button" className={button.dangerOutline} onClick={retry}>
                {retryLabel}
              </button>
            )
          )}
          {children}
        </div>
      )}
    </StatePanelFrame>
  )
}
