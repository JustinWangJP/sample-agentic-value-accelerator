import { useEffect, useState, type ReactNode } from 'react'
import { ApiError, toApiError, type ApiClient } from '../api/client'
import { DetailBreadcrumb } from '../components/NavigationControls'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { LoadingState, PageHeader } from '../components/StatePanel'
import { useNavigation } from '../routing/navigation'
import { canOpenScreen, type DraftsScreen } from '../routing/routes'
import { callout } from '../ui/classes'
import type { DraftDetail } from './api'
import { DraftEditor, NewDraftScreen, type BackOptions } from './DraftEditor'
import { DraftList } from './DraftList'
import { SubmissionView } from './SubmissionView'
import type { ScreenProps } from '../shell/screenProps'

type Props = ScreenProps & { teams: string[] }

const DRAFTS_LIST: DraftsScreen = { kind: 'list' }

/**
 * Publisher の下書き・申請。一覧（#/drafts）・作成（#/drafts/new）・編集（#/drafts/<レコード ID>）・
 * 申請（#/drafts/<レコード ID>/submit）を、URL のハッシュで切り替える
 */
export function RecordDraftsView({ api, roles, teams, onRelogin }: Props) {
  const { route, navigate } = useNavigation()
  const screen = route.screen === 'drafts' ? route.view : DRAFTS_LIST
  // 編集画面から申請へ進むときに渡す、保存済みの下書き。URL から直接開いたときは取得し直す
  const [savedForSubmit, setSavedForSubmit] = useState<DraftDetail | null>(null)
  const allowed = canOpenScreen('drafts', roles) && teams.length > 0

  if (!canOpenScreen('drafts', roles)) return null
  if (!allowed)
    return (
      <section aria-labelledby="drafts-heading">
        <PageHeader headingId="drafts-heading" title="下書きの管理" />
        <p className={callout.warning}>作成・編集には所属チームが必要です。管理者に連絡してください。</p>
      </section>
    )

  function go(view: DraftsScreen, options: BackOptions = {}) {
    navigate({ screen: 'drafts', view }, options)
  }
  const back = (options: BackOptions = {}) => go(DRAFTS_LIST, options)

  switch (screen.kind) {
    case 'list':
      return (
        <DraftList
          api={api}
          teams={teams}
          onCreate={() => go({ kind: 'new' })}
          onEdit={(recordId) => go({ kind: 'edit', recordId })}
          onRelogin={onRelogin}
        />
      )
    case 'new':
      return (
        <NewDraftScreen
          api={api}
          teams={teams}
          onBack={back}
          onSubmitDraft={(detail) => {
            setSavedForSubmit(detail)
            go({ kind: 'submit', recordId: detail.recordId })
          }}
          onRelogin={onRelogin}
        />
      )
    case 'edit':
      return (
        <DraftLoader key={`edit-${screen.recordId}`} api={api} recordId={screen.recordId} title="下書きを編集" onBack={back} onRelogin={onRelogin}>
          {(detail) => (
            <DraftEditor
              api={api}
              initial={{ kind: 'edit', detail }}
              teams={teams}
              onBack={back}
              onSubmitDraft={(saved) => {
                setSavedForSubmit(saved)
                go({ kind: 'submit', recordId: saved.recordId })
              }}
              onRelogin={onRelogin}
            />
          )}
        </DraftLoader>
      )
    case 'submit':
      return (
        <DraftLoader
          key={`submit-${screen.recordId}`}
          api={api}
          recordId={screen.recordId}
          title="下書きを申請"
          initial={savedForSubmit?.recordId === screen.recordId ? savedForSubmit : null}
          onBack={back}
          onRelogin={onRelogin}
        >
          {(detail) => <SubmissionView api={api} detail={detail} onBack={() => back()} onRelogin={onRelogin} />}
        </DraftLoader>
      )
  }
}

type LoaderState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: DraftDetail }
  | { kind: 'error'; error: ApiError }

/** 編集・申請の画面で、対象の下書きを取得してから中身を出す。initial があれば取得しない */
function DraftLoader({
  api,
  recordId,
  title,
  initial = null,
  onBack,
  onRelogin,
  children,
}: {
  api: ApiClient
  recordId: string
  title: string
  initial?: DraftDetail | null
  onBack: (options?: BackOptions) => void
  onRelogin: () => void
  children: (detail: DraftDetail) => ReactNode
}) {
  const [state, setState] = useState<LoaderState>(() =>
    initial ? { kind: 'ready', detail: initial } : { kind: 'loading' },
  )
  const loading = state.kind === 'loading'
  useEffect(() => {
    if (!loading) return
    let active = true
    api.getDraft(recordId).then(
      (detail) => {
        if (active) setState({ kind: 'ready', detail })
      },
      (failure: unknown) => {
        if (active) setState({ kind: 'error', error: toApiError(failure, '下書きを取得できませんでした。') })
      },
    )
    return () => {
      active = false
    }
  }, [api, recordId, loading])

  if (state.kind === 'ready') return <>{children(state.detail)}</>
  return (
    <section aria-label={title} className="space-y-4">
      <DetailBreadcrumb
        parentTo={{ screen: 'drafts', view: DRAFTS_LIST }}
        parentLabel="下書き・申請"
        current={title}
        onBack={() => onBack()}
      />
      {state.kind === 'loading' ? (
        <LoadingState>下書きを取得しています…</LoadingState>
      ) : (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={() => setState({ kind: 'loading' })} />
      )}
    </section>
  )
}
