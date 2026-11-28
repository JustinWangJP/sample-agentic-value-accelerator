import type { AriaRole, ReactNode } from 'react'
import { pageTitle, statePanel } from '../ui/classes'

// 状態パネル。0 件・読み込み中・準備中・失敗を、枠の色とバッジで区別する（CODING_STANDARDS.md）。
// API の失敗（準備中・401・403・それ以外）の出し分けは RequestErrorAlert が行い、枠はここで共有する

/** 状態パネルの枠。tone で色を、badge で状態の名前を示す */
export function StatePanelFrame({
  tone,
  badge,
  role,
  className = '',
  children,
}: {
  tone: keyof typeof statePanel
  badge?: ReactNode
  role?: AriaRole
  className?: string
  children: ReactNode
}) {
  return (
    <div role={role} className={`${statePanel[tone].frame} ${className}`}>
      {badge && <span className={statePanel[tone].badge}>{badge}</span>}
      {children}
    </div>
  )
}

/** 読み込み中。文言は role="status" の段落に置く（支援技術に読み上げさせる） */
export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <StatePanelFrame tone="neutral" className="flex flex-col items-center justify-center text-center">
      <div className="w-7 h-7 rounded-full border-2 border-slate-200 border-t-blue-600 animate-spin" aria-hidden="true" />
      <p role="status" className="text-sm font-medium text-slate-700">
        {children}
      </p>
    </StatePanelFrame>
  )
}

/** 正常に応答して 0 件だったこと。失敗や未取得とは区別する */
export function EmptyState({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <StatePanelFrame tone="neutral" badge="0 件（正常応答）" className="text-center">
      <p className={`text-sm font-semibold ${statePanel.neutral.title}`}>{children}</p>
      {hint && <p className={`text-xs ${statePanel.neutral.detail}`}>{hint}</p>}
    </StatePanelFrame>
  )
}

/** 業務ロールが足りず、この画面を開けないこと。再ログイン・再試行しても解決しないため、どちらのボタンも出さない */
export function ForbiddenState({ screenLabel }: { screenLabel: string }) {
  return (
    <StatePanelFrame tone="danger" badge="失敗（403 FORBIDDEN）" role="alert">
      <h2 className={`text-sm font-semibold ${statePanel.danger.title}`}>{screenLabel}を表示できません</h2>
      <p className={`text-xs ${statePanel.danger.detail}`}>
        この画面を利用する業務ロールがありません。ログインし直しても、再試行しても解決しないため、再ログイン・再試行のボタンは表示しません。管理者に業務ロールを確認してください。
      </p>
    </StatePanelFrame>
  )
}

/** 画面の見出しと説明。右側に画面の主操作を置ける */
export function PageHeader({
  title,
  description,
  actions,
  headingId,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  headingId?: string
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 id={headingId} className={pageTitle}>
          {title}
        </h1>
        {description && <div className="mt-1 text-sm text-slate-600 space-y-1">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
