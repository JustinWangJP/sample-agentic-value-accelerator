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
  backDisabled = false,
  children,
}: {
  parentTo: Route
  parentLabel: string
  current: string
  onBack: () => void
  backDisabled?: boolean
  children?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <nav aria-label="パンくずリスト" className="flex items-center gap-2 text-xs text-slate-500">
        <RouteLink to={parentTo} className="hover:text-slate-900">
          {parentLabel}
        </RouteLink>
        <span aria-hidden="true">/</span>
        <span className="text-slate-900 font-medium">{current}</span>
      </nav>
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <button type="button" className={button.small} disabled={backDisabled} onClick={onBack}>
          一覧に戻る
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
