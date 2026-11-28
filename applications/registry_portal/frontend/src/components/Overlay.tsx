import { useEffect, useId, useRef, type ReactNode } from 'react'

/** Escape で閉じる。開いたときに、ダイアログの中へフォーカスを移す */
function useOverlayBehavior(onClose: () => void) {
  const panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })
  useEffect(() => {
    const node = panel.current
    const target = node?.querySelector<HTMLElement>('[data-autofocus]') ?? node
    target?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])
  return panel
}

type ModalProps = {
  title: string
  /** 取り消せない操作の確認は danger にする */
  tone?: 'default' | 'danger'
  onClose: () => void
  children: ReactNode
  /** ダイアログ下部の操作ボタン */
  actions: ReactNode
  /** 取り消せない操作の最終確認は alertdialog にする */
  role?: 'dialog' | 'alertdialog'
  /** 見出しと別に、支援技術向けの名前を付ける */
  ariaLabel?: string
}

/**
 * 確認ダイアログ（モーダル）。React の木の中に描画し、ポータルは使わない（画面ごとのテストで同じコンテナから操作できる）。
 * 背景のクリックでは閉じない（入力中の内容や確認の取り違えを防ぐ）
 */
export function Modal({ title, tone = 'default', onClose, children, actions, role = 'dialog', ariaLabel }: ModalProps) {
  const titleId = useId()
  const panel = useOverlayBehavior(onClose)
  const titleClass = tone === 'danger' ? 'text-rose-900' : 'text-slate-900'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="fixed inset-0 bg-slate-900/40" aria-hidden="true" />
      <div
        ref={panel}
        role={role}
        aria-modal="true"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabel ? undefined : titleId}
        tabIndex={-1}
        className="relative w-full max-w-lg rounded-xl bg-white shadow-2xl outline-none"
      >
        <div className="px-5 py-4 border-b border-slate-200">
          <h2 id={titleId} className={`text-base font-semibold ${titleClass}`}>
            {title}
          </h2>
        </div>
        <div className="px-5 py-4 space-y-3 text-sm text-slate-700">{children}</div>
        <div className="px-5 py-3 bg-slate-50 border-t border-slate-200 rounded-b-xl flex flex-wrap justify-end gap-2">
          {actions}
        </div>
      </div>
    </div>
  )
}

type DrawerProps = {
  title: string
  subtitle?: string
  onClose: () => void
  children: ReactNode
  /** 開く側。履歴は右、モバイルメニューは左 */
  side?: 'left' | 'right'
}

/** 端からのスライドオーバー（操作と審査の履歴・モバイルメニュー）。背景のクリックと Escape で閉じる */
export function Drawer({ title, subtitle, onClose, children, side = 'right' }: DrawerProps) {
  const titleId = useId()
  const panel = useOverlayBehavior(onClose)
  return (
    <div className={`fixed inset-0 z-50 flex ${side === 'right' ? 'justify-end' : 'justify-start'}`}>
      <div className="fixed inset-0 bg-slate-900/40" aria-hidden="true" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative bg-white h-full shadow-2xl flex flex-col outline-none ${
          side === 'right' ? 'w-full max-w-lg' : 'w-72 max-w-[85vw]'
        }`}
      >
        <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-base font-semibold text-slate-900">
              {title}
            </h2>
            {subtitle && <p className="text-xs font-mono text-slate-500 break-all">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100"
            aria-label="閉じる"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}
