import { useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { DetailBreadcrumb } from '../components/NavigationControls'
import { OperationCheck } from '../components/OperationCheck'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { LoadingState } from '../components/StatePanel'
import {
  PUBLISHED_STATUS,
  STATUS_REASON_MAX_LENGTH,
  isUncertainWriteError,
  recordStatusLabel,
  settlesIdempotencyKey,
} from '../drafts/api'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import { badge, button, callout, card, fieldLabel, pageTitle } from '../ui/classes'
import type { DecisionKind, DecisionOperation, PendingDetail, Principal } from './api'
import { DiffTable, SnapshotSection } from './SubmissionComparison'

/** 決裁の種別ごとの画面の文言と見た目。AWS の遷移先は API の種別で決まる。公開承認は緑、却下は赤で見分ける */
const DECISIONS: Record<
  DecisionKind,
  { legend: string; reasonLabel: string; submit: string; failure: string; field: string; frame: string; button: string }
> = {
  approve: {
    legend: '公開承認',
    reasonLabel: '公開承認の理由（必須。判断の根拠として記録します）',
    submit: '公開承認する',
    failure: '公開承認できませんでした。',
    field: 'approveReason',
    frame: 'border-emerald-200 bg-emerald-50/30',
    button:
      'inline-flex items-center justify-center px-5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold shadow-xs disabled:opacity-50 disabled:cursor-not-allowed',
  },
  reject: {
    legend: '却下',
    reasonLabel: '却下理由（必須。Publisher が修正の参考にします）',
    submit: '却下する',
    failure: '却下できませんでした。',
    field: 'rejectReason',
    frame: 'border-rose-200 bg-rose-50/30',
    button:
      'inline-flex items-center justify-center px-5 py-2 rounded-lg border border-rose-300 bg-rose-50 hover:bg-rose-100 text-rose-800 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed',
  },
}

/** 決裁できない申請の警告（左に太線のある赤い囲み） */
const DECISION_BLOCKED = 'bg-rose-50 border-l-4 border-rose-600 rounded-r-xl p-4 text-sm text-rose-950'

function principalLabel(principal: Principal | null): string {
  return principal ? principal.subject : '不明'
}

type LoaderState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: PendingDetail }
  | { kind: 'error'; error: ApiError }

/** #/reviews/<レコード ID> の審査対象を取得してから、審査の画面を出す */
export function ReviewDetailLoader({
  api,
  recordId,
  onBack,
  onRelogin,
}: {
  api: ApiClient
  recordId: string
  onBack: () => void
  onRelogin: () => void
}) {
  const [state, setState] = useState<LoaderState>({ kind: 'loading' })
  const loading = state.kind === 'loading'
  useEffect(() => {
    if (!loading) return
    let active = true
    api.getReview(recordId).then(
      (detail) => {
        if (active) setState({ kind: 'ready', detail })
      },
      (failure: unknown) => {
        if (active) setState({ kind: 'error', error: toApiError(failure, '審査対象を取得できませんでした。') })
      },
    )
    return () => {
      active = false
    }
  }, [api, recordId, loading])

  if (state.kind === 'ready')
    return <ReviewDetailView api={api} initial={state.detail} onBack={onBack} onRelogin={onRelogin} />
  return (
    <section aria-label="審査する" className="space-y-4">
      <DetailBreadcrumb
        parentTo={{ screen: 'reviews', recordId: null }}
        parentLabel="審査待ち"
        current="審査詳細"
        onBack={onBack}
      />
      {state.kind === 'loading' ? (
        <LoadingState>審査対象を読み込んでいます…</LoadingState>
      ) : (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={() => setState({ kind: 'loading' })} />
      )}
    </section>
  )
}

type DecideState =
  | { kind: 'idle' }
  | { kind: 'deciding' }
  /** retry は、同じ Idempotency-Key で送り直せる決裁の種別。再取得の失敗など決裁以外の失敗では null */
  | { kind: 'error'; error: ApiError; retry: DecisionKind | null }
  | { kind: 'result'; operation: DecisionOperation }

/** 申請のスナップショットに基づく公開承認・却下 */
function ReviewDetailView({
  api,
  initial,
  onBack,
  onRelogin,
}: {
  api: ApiClient
  initial: PendingDetail
  onBack: () => void
  onRelogin: () => void
}) {
  const [detail, setDetail] = useState(initial)
  const [reasons, setReasons] = useState<Record<DecisionKind, string>>({ approve: '', reject: '' })
  const [keys, setKeys] = useState<Record<DecisionKind, string>>(() => ({
    approve: crypto.randomUUID(),
    reject: crypto.randomUUID(),
  }))
  const [state, setState] = useState<DecideState>({ kind: 'idle' })
  const snapshot = detail.snapshot
  const uncertain = state.kind === 'error' && state.retry !== null && isUncertainWriteError(state.error)
  const decided =
    state.kind === 'result' && state.operation.operationState === 'SUCCEEDED'
  const deciding = state.kind === 'deciding'
  // 書き換えられたスナップショットや無効になった申請は、送っても 409 になるため決裁させない
  const decidable = snapshot !== null && detail.snapshotIntact && !detail.invalidated
  const locked = deciding || decided || !decidable

  function renewKey(kind: DecisionKind) {
    setKeys((current) => ({ ...current, [kind]: crypto.randomUUID() }))
  }

  async function decide(kind: DecisionKind, retry = false) {
    if (!snapshot) return
    const reason = reasons[kind].trim()
    if (!reason) return
    if (locked && !retry) return
    setState({ kind: 'deciding' })
    try {
      const operation = await api.decideSubmission(
        kind,
        detail.recordId,
        { snapshotHash: snapshot.snapshotHash, reason },
        keys[kind],
      )
      if (operation.operationState === 'FAILED') renewKey(kind)
      setState({ kind: 'result', operation })
    } catch (failure) {
      const error = toApiError(failure, DECISIONS[kind].failure)
      if (settlesIdempotencyKey(error)) renewKey(kind)
      setState({ kind: 'error', error, retry: kind })
    }
  }

  async function reload() {
    setState({ kind: 'idle' })
    try {
      setDetail(await api.getReview(detail.recordId))
    } catch (failure) {
      setState({
        kind: 'error',
        error: toApiError(failure, '審査対象を再取得できませんでした。'),
        retry: null,
      })
    }
  }

  return (
    <section aria-labelledby="review-heading" className="space-y-6">
      <DetailBreadcrumb
        parentTo={{ screen: 'reviews', recordId: null }}
        parentLabel="審査待ち"
        current="審査詳細"
        onBack={onBack}
        backDisabled={deciding}
      >
        <div className="w-56">
          <RecordHistoryToggle api={api} recordId={detail.recordId} onRelogin={onRelogin} />
        </div>
      </DetailBreadcrumb>
      <section className={`${card} p-6`}>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className={badge.warning}>{recordStatusLabel(detail.status)}</span>
          <span className={badge.neutral}>{RECORD_TYPE_LABELS[detail.recordType]}</span>
        </div>
        <h1 id="review-heading" className={`${pageTitle} break-all`}>
          <span className="sr-only">審査する: </span>
          {detail.displayName}
        </h1>
        <p className="mt-1 text-sm text-slate-700">
          {detail.displayName}（{RECORD_TYPE_LABELS[detail.recordType]}）・版 {detail.recordVersion}
        </p>
        <p className="mt-0.5 text-xs text-slate-600">
          所有チーム {detail.ownerTeam ?? '不明'} /{' '}
          {detail.publishedVersion ? `公開中の版 ${detail.publishedVersion}` : '公開版なし（初回公開）'}
        </p>
        {detail.deprecationRequest && (
          <p role="alert" className={`mt-4 ${callout.danger}`}>
            このレコードには未解決の廃止依頼があります（理由: {detail.deprecationRequest.reason}・依頼者{' '}
            {detail.deprecationRequest.requestedBy.subject}）。依頼者の意図を確認してから決裁してください。
          </p>
        )}
      </section>
      {!snapshot ? (
        <p role="alert" className={DECISION_BLOCKED}>
          {detail.submissionState === 'MISSING'
            ? '申請スナップショットを取得できないため、決裁できません。管理者に連絡してください。'
            : 'このレコードにはポータル経由の申請がないため、決裁できません。管理者に連絡してください。'}
        </p>
      ) : (
        <>
          {detail.invalidated && (
            <p role="alert" className={DECISION_BLOCKED}>
              この申請は審査中の編集で無効になっています。決裁できません。一覧で最新の状態を確認してください。
            </p>
          )}
          {!detail.snapshotIntact && (
            <p role="alert" className={DECISION_BLOCKED}>
              保存された申請スナップショットが申請時の内容と一致しません。決裁できません。Publisher
              に再申請を依頼し、管理者に連絡してください。
            </p>
          )}
          <SnapshotSection snapshot={snapshot} />
          <section aria-labelledby="review-diff-heading" className={`${card} overflow-hidden`}>
            <div className="px-6 py-4 border-b border-slate-200">
              <h2 id="review-diff-heading" className="text-base font-semibold text-slate-900">
                公開中の版との差分
              </h2>
              <p className="text-xs text-slate-500 mt-0.5">
                変更のある項目を強調表示しています。決裁は固定された申請スナップショットに対して実行します（差分は参考情報）。
              </p>
            </div>
            {detail.publishedState === 'MISSING' ? (
              <p role="alert" className={`m-6 ${callout.danger}`}>
                公開中の版 {detail.publishedVersion} のスナップショットは未取得です。差分を確認できないため、
                管理者に連絡してください。
              </p>
            ) : detail.published ? (
              <>
                <p className="px-6 pt-4 text-xs text-slate-600">
                  公開承認した Curator: {principalLabel(detail.published.approvedBy)}
                </p>
                <DiffTable
                  next={{ ...snapshot, recordVersion: detail.recordVersion }}
                  published={detail.published}
                />
              </>
            ) : (
              <p className="px-6 py-4 text-sm text-slate-600">公開版がないため、すべての項目が初めて公開されます。</p>
            )}
          </section>
          <section className="bg-white border-2 border-slate-300 rounded-xl shadow-xs p-6 space-y-4">
            <div>
              <h2 className="text-base font-semibold text-slate-900">決裁（公開承認 / 却下）</h2>
              <p className="text-xs text-slate-600 mt-0.5">
                公開承認はカタログでの発見を許可するものです（実行の許可とは別に管理します）。どちらの決裁でも理由の入力が必須です。
              </p>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {(['approve', 'reject'] as const).map((kind) => (
                <form
                  key={kind}
                  className={`rounded-xl border p-4 ${DECISIONS[kind].frame}`}
                  onSubmit={(event) => {
                    event.preventDefault()
                    void decide(kind)
                  }}
                >
                  <fieldset disabled={locked} className="space-y-3">
                    <legend className="text-sm font-semibold text-slate-900">{DECISIONS[kind].legend}</legend>
                    <label className={fieldLabel}>
                      {DECISIONS[kind].reasonLabel}
                      <textarea
                        name={DECISIONS[kind].field}
                        required
                        maxLength={STATUS_REASON_MAX_LENGTH}
                        rows={3}
                        value={reasons[kind]}
                        onChange={(event) =>
                          setReasons((current) => ({ ...current, [kind]: event.target.value }))
                        }
                      />
                    </label>
                    <button type="submit" className={DECISIONS[kind].button} disabled={locked || !reasons[kind].trim()}>
                      {DECISIONS[kind].submit}
                    </button>
                  </fieldset>
                </form>
              ))}
            </div>
            {state.kind === 'deciding' && <LoadingState>決裁しています…</LoadingState>}
            {state.kind === 'error' && (
              <RequestErrorAlert error={state.error} onRelogin={onRelogin}>
                {state.error.status === 409 && (
                  <p className="text-xs text-rose-800">
                    審査した内容が現在の申請と一致しません。最新の申請を読み込んでから決裁してください。
                  </p>
                )}
                {uncertain && state.retry !== null && (
                  <p className="text-xs text-rose-800 space-x-2">
                    <span>決裁結果が不明です。同じ内容で再試行すると、同じ操作として確定を試みます。</span>
                    <button
                      type="button"
                      className={button.dangerOutline}
                      disabled={deciding}
                      onClick={() => {
                        if (state.retry !== null) void decide(state.retry, true)
                      }}
                    >
                      同じ内容で決裁を再試行
                    </button>
                  </p>
                )}
                {state.error.status === 409 && (
                  <button type="button" className={button.dangerOutline} onClick={() => void reload()}>
                    最新の申請を再読み込み
                  </button>
                )}
              </RequestErrorAlert>
            )}
            {state.kind === 'result' && state.operation.operationState === 'SUCCEEDED' && (
              <p role="status" className={callout.success}>
                {state.operation.status === PUBLISHED_STATUS
                  ? `公開承認しました（版 ${state.operation.recordVersion}）。カタログ検索への反映は数分かかることがあります（検索への反映待ち）。`
                  : '却下しました。Publisher が編集して再申請します。'}
              </p>
            )}
            {state.kind === 'result' && state.operation.operationState === 'VERIFYING' && (
              <OperationCheck
                operationId={state.operation.operationId}
                load={(id) => api.getDecisionOperation(id)}
                onDone={(operation) => setState({ kind: 'result', operation })}
                onRelogin={onRelogin}
              />
            )}
            {state.kind === 'result' && state.operation.operationState === 'FAILED' && (
              <p role="alert" className={callout.danger}>
                {state.operation.message}
              </p>
            )}
          </section>
        </>
      )}
    </section>
  )
}
