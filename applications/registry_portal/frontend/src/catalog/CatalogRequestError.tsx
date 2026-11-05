import type { ReactNode } from 'react'

type Props = {
  error: { message: string; requiresLogin: boolean }
  /** 省略すると再試行ボタンを出さない（再試行しても解決しないエラー） */
  onRetry?: () => void
  onRelogin: () => void
  /** エラーに応じた追加の操作 */
  children?: ReactNode
}

/** API の失敗を、0件とは別の状態として表示する。401 のときは再試行ではなくログインし直しを促す */
export function CatalogRequestError({ error, onRetry, onRelogin, children }: Props) {
  return (
    <div role="alert" className="error">
      <p>{error.message}</p>
      {error.requiresLogin ? (
        <button type="button" onClick={onRelogin}>
          もう一度ログインする
        </button>
      ) : (
        onRetry && (
          <button type="button" onClick={onRetry}>
            再試行
          </button>
        )
      )}
      {children}
    </div>
  )
}
