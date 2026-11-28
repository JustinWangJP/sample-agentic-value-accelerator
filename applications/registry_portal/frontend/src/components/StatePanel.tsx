import type { ReactNode } from 'react'

// 一覧領域の状態パネル。0 件・読み込み中・失敗（RequestErrorAlert）・準備中を見た目で区別する（CODING_STANDARDS.md）

const PANEL_FRAME = 'rounded-xl p-5 space-y-2'
const PANEL_BADGE = 'inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold'

/** 読み込み中。文言は role="status" の段落に置く（支援技術に読み上げさせる） */
export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <div className={`bg-white border border-slate-200 ${PANEL_FRAME} flex flex-col items-center justify-center text-center`}>
      <div className="w-7 h-7 rounded-full border-2 border-slate-200 border-t-blue-600 animate-spin" aria-hidden="true" />
      <p role="status" className="text-sm font-medium text-slate-700">
        {children}
      </p>
    </div>
  )
}

/** 正常に応答して 0 件だったこと。失敗や未取得とは区別する */
export function EmptyState({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className={`bg-white border border-slate-200 ${PANEL_FRAME} text-center`}>
      <span className={`${PANEL_BADGE} bg-slate-100 text-slate-600`}>0 件（正常応答）</span>
      <p className="text-sm font-semibold text-slate-900">{children}</p>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

/** 業務ロールが足りず、この画面を開けないこと。再ログイン・再試行しても解決しないため、どちらのボタンも出さない */
export function ForbiddenState({ screenLabel }: { screenLabel: string }) {
  return (
    <div role="alert" className={`bg-rose-50 border border-rose-200 ${PANEL_FRAME}`}>
      <span className={`${PANEL_BADGE} font-mono bg-rose-100 text-rose-800`}>失敗（403 FORBIDDEN）</span>
      <h2 className="text-sm font-semibold text-rose-950">{screenLabel}を表示できません</h2>
      <p className="text-xs text-rose-800">
        この画面を利用する業務ロールがありません。ログインし直しても、再試行しても解決しないため、再ログイン・再試行のボタンは表示しません。管理者に業務ロールを確認してください。
      </p>
    </div>
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
        <h1 id={headingId} className="text-2xl font-bold tracking-tight text-slate-900">
          {title}
        </h1>
        {description && <div className="mt-1 text-sm text-slate-600 space-y-1">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
