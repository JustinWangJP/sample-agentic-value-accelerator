import { useEffect, useState } from 'react'
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

  return (
    <main className="page">
      <h1>Registry ポータル</h1>
      {state === null || state.status === 'redirecting' ? (
        <p role="status">ログインしています…</p>
      ) : state.status === 'error' ? (
        <div role="alert" className="error">
          <p>{state.message}</p>
          {state.canRetryLogin && (
            <button type="button" onClick={() => window.location.assign('/')}>
              もう一度ログインする
            </button>
          )}
        </div>
      ) : (
        <section aria-labelledby="me-heading">
          <h2 id="me-heading">自分の主体とロール</h2>
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
      )}
    </main>
  )
}
