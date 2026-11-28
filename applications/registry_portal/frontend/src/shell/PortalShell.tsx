import { useEffect, useState, type ReactNode } from 'react'
import type { Me, Role } from '../api/client'
import { Drawer } from '../components/Overlay'
import { POPOVER_PANEL, POPOVER_TRIGGER, usePopover } from '../components/usePopover'
import { useNavigation } from '../routing/navigation'
import { RouteLink } from '../routing/NavigationProvider'
import { DEFAULT_ROUTE } from '../routing/routes'
import { visibleNavSections, type NavItem } from './navigation'

const NAV_LINK = {
  current: 'flex items-center px-3 py-2 rounded-lg text-sm font-semibold bg-blue-50 text-blue-700',
  normal: 'flex items-center px-3 py-2 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100',
}
const NAV_SECTION_TITLE = 'px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400'

/** ロゴ（ヘッダーの左端）。ログイン後はカタログへのリンクにする */
export function PortalLogo({ linked = true }: { linked?: boolean }) {
  const content = (
    <>
      <span className="w-8 h-8 rounded-lg bg-blue-600 text-white font-bold text-sm inline-flex items-center justify-center shadow-xs">
        R
      </span>
      <span className="leading-tight">
        <span className="block font-semibold text-sm tracking-tight">Registry ポータル</span>
        <span className="block text-[11px] text-slate-500">AWS Agent Registry 管理・発見基盤</span>
      </span>
    </>
  )
  const className = 'flex items-center gap-2.5 text-slate-900 no-underline'
  return linked ? (
    <RouteLink to={DEFAULT_ROUTE} className={className}>
      {content}
    </RouteLink>
  ) : (
    <span className={className}>{content}</span>
  )
}

/** 業務ロール別のナビゲーション。デスクトップのサイドバーとモバイルメニューで共通 */
export function PortalNav({ roles, label, onNavigate }: { roles: readonly Role[]; label: string; onNavigate?: () => void }) {
  const { route } = useNavigation()
  return (
    <nav aria-label={label} className="space-y-6">
      {visibleNavSections(roles).map((section) => (
        <div key={section.title}>
          <div className={NAV_SECTION_TITLE}>{section.title}</div>
          <ul className="space-y-1">
            {section.items.map((navItem: NavItem) => {
              const current = navItem.screen === route.screen
              return (
                <li key={navItem.screen}>
                  <RouteLink
                    to={navItem.route}
                    aria-current={current ? 'page' : undefined}
                    className={current ? NAV_LINK.current : NAV_LINK.normal}
                    onClick={onNavigate}
                  >
                    {navItem.label}
                  </RouteLink>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}

/** ヘッダー右端の、自分の主体・業務ロール・所属チーム */
export function PrincipalMenu({ me }: { me: Me }) {
  const { open, setOpen, container } = usePopover()
  const roleSummary = me.roles.join(' / ')
  return (
    <div className="relative" ref={container}>
      <button
        type="button"
        aria-expanded={open}
        aria-label={`自分の主体・業務ロール・所属チーム（${roleSummary || '業務ロールなし'}）`}
        onClick={() => setOpen(!open)}
        className={POPOVER_TRIGGER}
      >
        <span className="w-6 h-6 rounded-full bg-slate-800 text-white text-xs font-semibold inline-flex items-center justify-center" aria-hidden="true">
          主
        </span>
        <span className="hidden md:inline max-w-44 truncate text-xs font-medium" aria-hidden="true">
          {roleSummary}
        </span>
      </button>
      {/* 閉じている間も描画しておき、hidden で隠す（支援技術からも隠れる） */}
      <div className={`${POPOVER_PANEL} p-4`} hidden={!open}>
        <h2 className="text-sm font-semibold text-slate-900 mb-3">自分の主体・業務ロール・所属チーム</h2>
        <dl className="space-y-2.5 text-xs">
          <div>
            <dt className="text-slate-500 font-medium">発行元（iss）</dt>
            <dd className="mt-0.5 font-mono text-slate-800 break-all bg-slate-50 px-2 py-1 rounded border border-slate-200">
              {me.principal.issuer}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 font-medium">主体（sub）</dt>
            <dd className="mt-0.5 font-mono text-slate-800 break-all bg-slate-50 px-2 py-1 rounded border border-slate-200">
              {me.principal.subject}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 font-medium">業務ロール</dt>
            <dd className="mt-0.5 text-slate-900 font-medium">{me.roles.join('、')}</dd>
          </div>
          <div>
            <dt className="text-slate-500 font-medium">所属チーム</dt>
            <dd className="mt-0.5 text-slate-900">{me.teams.length > 0 ? me.teams.join('、') : '所属チームなし'}</dd>
          </div>
        </dl>
        <p className="mt-3 pt-3 border-t border-slate-200 text-[11px] text-slate-500 leading-relaxed">
          アクセストークンはメモリー上だけで保持しています。ページを再読み込みすると、もう一度 SSO でログインします。
        </p>
      </div>
    </div>
  )
}

/** ヘッダーの枠。ログイン前・起動失敗の画面でも同じ見た目にする（ログイン前はロゴをリンクにしない） */
export function HeaderBar({ start, end, linked = true }: { start?: ReactNode; end?: ReactNode; linked?: boolean }) {
  return (
    <header className="sticky top-0 z-30 bg-white border-b border-slate-200 h-15 flex items-center px-4 lg:px-6">
      <div className="w-full max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          {start}
          <PortalLogo linked={linked} />
        </div>
        {end && <div className="flex items-center gap-2">{end}</div>}
      </div>
    </header>
  )
}

type ShellProps = {
  me: Me
  /** ヘッダーの通知欄 */
  notifications: ReactNode
  children: ReactNode
}

/** ログイン後の共通レイアウト。上部ヘッダー・左サイドバー（業務ロール別）・モバイルメニュー・メイン */
export function PortalShell({ me, notifications, children }: ShellProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const { route } = useNavigation()
  // 画面を切り替えたら先頭から表示する
  useEffect(() => {
    window.scrollTo?.(0, 0)
  }, [route])
  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <HeaderBar
        start={
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            className="lg:hidden inline-flex items-center justify-center p-2 rounded-lg text-slate-600 hover:bg-slate-100"
            aria-label="メニューを開く"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        }
        end={
          <>
            {notifications}
            <PrincipalMenu me={me} />
          </>
        }
      />
      <div className="flex-1 flex max-w-screen-2xl w-full mx-auto">
        <aside className="hidden lg:block w-64 shrink-0 border-r border-slate-200 bg-white px-3 py-5">
          <PortalNav roles={me.roles} label="メインナビゲーション" />
        </aside>
        <main className="flex-1 min-w-0 px-4 sm:px-6 lg:px-8 py-6">{children}</main>
      </div>
      {menuOpen && (
        <Drawer title="メニュー" side="left" onClose={() => setMenuOpen(false)}>
          <PortalNav roles={me.roles} label="モバイルナビゲーション" onNavigate={() => setMenuOpen(false)} />
        </Drawer>
      )}
    </div>
  )
}
