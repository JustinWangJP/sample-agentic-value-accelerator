import { useEffect, useState } from 'react'
import { AdministrationView } from './administration/AdministrationView'
import type { ApiClient, Me } from './api/client'
import { CatalogView } from './catalog/CatalogView'
import { ForbiddenState, PageHeader } from './components/StatePanel'
import { canViewAllHistory } from './drafts/api'
import { RecordDraftsView } from './drafts/RecordDraftsView'
import { NotificationsView } from './notifications/NotificationsView'
import { RecordPublicationView } from './publication/RecordPublicationView'
import { RecoveryView } from './recovery/RecoveryView'
import { ReviewsView } from './reviews/ReviewsView'
import { browserHashLocation, type HashLocation } from './routing/location'
import { useNavigation } from './routing/navigation'
import { NavigationProvider } from './routing/NavigationProvider'
import { canOpenScreen } from './routing/routes'
import { SCREEN_LABELS } from './shell/navigation'
import { HeaderBar, PortalShell } from './shell/PortalShell'
import type { StartupState } from './startup'
import { button } from './ui/classes'

type Props = { startup: Promise<StartupState> }

export default function App({ startup }: Props) {
  const [state, setState] = useState<StartupState | null>(null)
  const [location] = useState(() => browserHashLocation())

  useEffect(() => {
    let active = true
    startup.then((result) => {
      if (active) setState(result)
    })
    return () => {
      active = false
    }
  }, [startup])

  return <StartupView state={state} location={location} />
}

/** 起動処理の状態を表示する。state が null の間は起動処理の完了待ち */
export function StartupView({ state, location }: { state: StartupState | null; location: HashLocation }) {
  if (state?.status === 'ready') {
    return (
      <NavigationProvider location={location}>
        <PortalShell me={state.me} notifications={<NotificationsView api={state.api} onRelogin={state.relogin} />}>
          <CurrentScreen me={state.me} api={state.api} onRelogin={state.relogin} />
        </PortalShell>
      </NavigationProvider>
    )
  }
  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <HeaderBar linked={false} />
      <main className="flex-1 w-full max-w-xl mx-auto px-4 py-12">
        {state === null || state.status === 'redirecting' ? (
          <div className="bg-white border border-slate-200 rounded-xl p-6 flex flex-col items-center gap-3 text-center">
            <div className="w-7 h-7 rounded-full border-2 border-slate-200 border-t-blue-600 animate-spin" aria-hidden="true" />
            <p role="status" className="text-sm font-medium text-slate-700">
              ログインしています…
            </p>
          </div>
        ) : (
          <div role="alert" className="bg-rose-50 border border-rose-200 rounded-xl p-5 space-y-3">
            <p className="text-sm font-semibold text-rose-950">{state.message}</p>
            {state.retryLogin && (
              <button type="button" className={button.danger} onClick={state.retryLogin}>
                もう一度ログインする
              </button>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

/** ルートに対応する画面。業務ロールが足りない画面は、403 相当の表示にする（API 側でも同じ権限を検査する） */
function CurrentScreen({ me, api, onRelogin }: { me: Me; api: ApiClient; onRelogin: () => void }) {
  const { route } = useNavigation()
  if (!canOpenScreen(route.screen, me.roles)) {
    const label = SCREEN_LABELS[route.screen]
    return (
      <>
        <PageHeader title={label} />
        <ForbiddenState screenLabel={label} />
      </>
    )
  }
  switch (route.screen) {
    case 'catalog':
      return (
        <CatalogView
          api={api}
          canViewHistory={canViewAllHistory(me.roles)}
          canManagePublication={me.roles.includes('Curator')}
          canCreateDraft={me.roles.includes('Publisher')}
          onRelogin={onRelogin}
        />
      )
    case 'drafts':
      return <RecordDraftsView api={api} roles={me.roles} teams={me.teams} onRelogin={onRelogin} />
    case 'reviews':
      return <ReviewsView api={api} roles={me.roles} onRelogin={onRelogin} />
    case 'publication':
      return <RecordPublicationView api={api} roles={me.roles} onRelogin={onRelogin} />
    case 'administration':
      return <AdministrationView api={api} roles={me.roles} onRelogin={onRelogin} />
    case 'recovery':
      return <RecoveryView api={api} roles={me.roles} onRelogin={onRelogin} />
  }
}
