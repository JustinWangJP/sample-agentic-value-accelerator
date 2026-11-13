import { useEffect, useState } from 'react'
import { CatalogView } from './catalog/CatalogView'
import { canViewAllHistory } from './drafts/api'
import { RecordDraftsView } from './drafts/RecordDraftsView'
import { RecordPublicationView } from './publication/RecordPublicationView'
import { RecoveryView } from './recovery/RecoveryView'
import { ReviewsView } from './reviews/ReviewsView'
import type { StartupState } from './startup'

type Props = { startup: Promise<StartupState> }

export default function App({ startup }: Props) {
  const [state, setState] = useState<StartupState | null>(null)

  useEffect(() => {
    let active = true
    startup.then((result) => {
      if (active) setState(result)
    })
    return () => {
      active = false
    }
  }, [startup])

  return <StartupView state={state} />
}

/** 起動処理の状態を表示する。state が null の間は起動処理の完了待ち */
export function StartupView({ state }: { state: StartupState | null }) {
  return (
    <main className="page">
      <h1>Registry ポータル</h1>
      {state === null || state.status === 'redirecting' ? (
        <p role="status">ログインしています…</p>
      ) : state.status === 'error' ? (
        <div role="alert" className="error">
          <p>{state.message}</p>
          {state.retryLogin && (
            <button type="button" onClick={state.retryLogin}>
              もう一度ログインする
            </button>
          )}
        </div>
      ) : (
        <>
          <section aria-labelledby="me-heading">
            <h2 id="me-heading">自分の主体・業務ロール・所属チーム</h2>
            <dl>
              <dt>発行元（iss）</dt>
              <dd>{state.me.principal.issuer}</dd>
              <dt>主体（sub）</dt>
              <dd>{state.me.principal.subject}</dd>
              <dt>業務ロール</dt>
              <dd>{state.me.roles.join('、')}</dd>
              <dt>所属チーム</dt>
              <dd>{state.me.teams.length > 0 ? state.me.teams.join('、') : '所属チームなし'}</dd>
            </dl>
            <p className="note">
              アクセストークンはメモリー上だけで保持しています。ページを再読み込みすると、もう一度 SSO
              でログインします。
            </p>
          </section>
          <CatalogView
            api={state.api}
            canViewHistory={canViewAllHistory(state.me.roles)}
            canManagePublication={state.me.roles.includes('Curator')}
            onRelogin={state.relogin}
          />
          <ReviewsView api={state.api} roles={state.me.roles} onRelogin={state.relogin} />
          <RecordPublicationView api={state.api} roles={state.me.roles} onRelogin={state.relogin} />
          <RecoveryView api={state.api} roles={state.me.roles} onRelogin={state.relogin} />
          <RecordDraftsView api={state.api} roles={state.me.roles} teams={state.me.teams} onRelogin={state.relogin} />
        </>
      )}
    </main>
  )
}
