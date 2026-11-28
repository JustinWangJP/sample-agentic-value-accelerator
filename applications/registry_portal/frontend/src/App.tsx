import { useEffect, useState } from 'react'
import { AdministrationView } from './administration/AdministrationView'
import type { ApiClient, Me } from './api/client'
import { CatalogView } from './catalog/CatalogView'
import { ForbiddenState, LoadingState, PageHeader, StatePanelFrame } from './components/StatePanel'
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
import { SCREEN_LABELS } from './shell/navSections'
import { HeaderBar, PortalShell } from './shell/PortalShell'
import type { ScreenProps } from './shell/screenProps'
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
          <LoadingState>ログインしています…</LoadingState>
        ) : (
          <StatePanelFrame tone="danger" role="alert">
            <p className="text-sm font-semibold text-rose-950">{state.message}</p>
            {state.retryLogin && (
              <button type="button" className={button.danger} onClick={state.retryLogin}>
                もう一度ログインする
              </button>
            )}
          </StatePanelFrame>
        )}
      </main>
    </div>
  )
}

/** ルートに対応する画面。業務ロールが足りない画面は、403 相当の表示にする（API 側でも同じ業務ロールを検査する） */
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
  const screen: ScreenProps = { api, roles: me.roles, onRelogin }
  switch (route.screen) {
    case 'catalog':
      return (
        <CatalogView
          api={api}
          canViewHistory={canViewAllHistory(me.roles)}
          canManagePublication={canOpenScreen('publication', me.roles)}
          canCreateDraft={canOpenScreen('drafts', me.roles)}
          onRelogin={onRelogin}
        />
      )
    case 'drafts':
      return <RecordDraftsView {...screen} teams={me.teams} />
    case 'reviews':
      return <ReviewsView {...screen} />
    case 'publication':
      return <RecordPublicationView {...screen} />
    case 'administration':
      return <AdministrationView {...screen} />
    case 'recovery':
      return <RecoveryView {...screen} />
  }
}
