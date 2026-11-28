import type { ReactNode } from 'react'
import { RouteLink } from '../routing/NavigationProvider'
import type { Route } from '../routing/routes'
import { button, pager } from '../ui/classes'

/** 詳細・編集・申請画面の上部に置くパンくずと「一覧に戻る」 */
export function DetailBreadcrumb({
  parentTo,
  parentLabel,
  current,
  onBack,
  backLabel = '一覧に戻る',
  backDisabled = false,
  className = '',
  children,
}: {
  /** 親の画面。省略したときは、親の名前をリンクにしない（申請の送信中など、戻る操作をボタンに限る画面） */
  parentTo?: Route
  parentLabel: string
  current: string
  onBack: () => void
  backLabel?: string
  backDisabled?: boolean
  className?: string
  children?: ReactNode
}) {
  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 ${className}`}>
      <nav aria-label="パンくずリスト" className="flex items-center gap-2 text-xs text-slate-500">
        {parentTo ? (
          <RouteLink to={parentTo} className="hover:text-slate-900">
            {parentLabel}
          </RouteLink>
        ) : (
          <span>{parentLabel}</span>
        )}
        <span aria-hidden="true">/</span>
        <span className="text-slate-900 font-medium">{current}</span>
      </nav>
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <button type="button" className={button.small} disabled={backDisabled} onClick={onBack}>
          {backLabel}
        </button>
      </div>
    </div>
  )
}

/** nextToken による「最初のページ / 次のページ」のページ送り */
export function CursorPager({
  ariaLabel,
  hasPrevious,
  hasNext,
  onFirst,
  onNext,
  className = pager,
}: {
  ariaLabel: string
  hasPrevious: boolean
  hasNext: boolean
  onFirst: () => void
  onNext: () => void
  className?: string
}) {
  return (
    <nav className={className} aria-label={ariaLabel}>
      <button type="button" className={button.small} disabled={!hasPrevious} onClick={onFirst}>
        最初のページ
      </button>
      <button type="button" className={button.small} disabled={!hasNext} onClick={onNext}>
        次のページ
      </button>
    </nav>
  )
}
