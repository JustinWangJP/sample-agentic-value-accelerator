import { useEffect, useState, type ReactNode } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { useNavigation } from '../routing/navigation'
import { RouteLink } from '../routing/NavigationProvider'
import { badge, button, callout, card, fieldLabel, pager } from '../ui/classes'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import {
  PUBLISHED_STATUS,
  STATUS_REASON_MAX_LENGTH,
  isUncertainWriteError,
  recordStatusLabel,
  settlesIdempotencyKey,
} from '../drafts/api'
import { elapsedDays } from '../elapsedDays'
import { RecordPublicationToggle } from '../publication/RecordPublicationPanel'
import {
  type DecisionKind,
  type DecisionOperation,
  type DeprecationRequestSummary,
  type PendingDetail,
  type PendingPage,
  type Principal,
  type PublishedBase,
  type RecordReference,
  type SubmissionSnapshot,
  type SubmissionSummary,
} from './api'
import { OperationCheck } from '../components/OperationCheck'

type Props = { api: ApiClient; roles: string[]; onRelogin: () => void }
type ListState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: PendingPage }
  | { kind: 'error'; error: ApiError }

/** 決裁の種別ごとの画面の文言。AWS の遷移先は API の種別で決まる */
const DECISION_COPY: Record<
  DecisionKind,
  { legend: string; reasonLabel: string; submit: string; failure: string; field: string }
> = {
  approve: {
    legend: '公開承認',
    reasonLabel: '公開承認の理由（必須。判断の根拠として記録します）',
    submit: '公開承認する',
    failure: '公開承認できませんでした。',
    field: 'approveReason',
  },
  reject: {
    legend: '却下',
    reasonLabel: '却下理由（必須。Publisher が修正の参考にします）',
    submit: '却下する',
    failure: '却下できませんでした。',
    field: 'rejectReason',
  },
}

function formatReferences(references: RecordReference[]): string {
  return (
    references.map((reference) => `${reference.recordId} @ ${reference.recordVersion}`).join('、') ||
    'ありません'
  )
}

function principalLabel(principal: Principal | null): string {
  return principal ? principal.subject : '不明'
}

/** 一覧の申請理由。申請がない場合と、申請はあるはずなのに読めない場合を区別する */
function submissionLine(item: SubmissionSummary): string {
  if (item.submissionState === 'MISSING')
    return '申請スナップショットを取得できません（管理者に連絡してください）'
  if (item.submissionState === 'NONE' || item.reason === null) return 'ポータル経由の申請なし'
  return item.reason
}

/** 廃止依頼の項目の状況。公開停止済みなら、廃止するか見送るかの判断待ち */
function requestProgress(item: DeprecationRequestSummary): string {
  const days = elapsedDays(item.requestedAt)
  const elapsed = days === null ? '' : `・${days} 日経過`
  return item.suspended ? `公開停止済み・廃止の判断待ち${elapsed}` : `未対応${elapsed}`
}

/** Curator の審査待ちの一覧と、申請のスナップショットに基づく公開承認・却下、廃止依頼への対応 */
export function ReviewsView({ api, roles, onRelogin }: Props) {
  const { route, navigate } = useNavigation()
  const recordId = route.screen === 'reviews' ? route.recordId : null
  if (!roles.includes('Curator')) return null
  if (recordId !== null)
    return (
      <ReviewDetailLoader
        key={recordId}
        api={api}
        recordId={recordId}
        onBack={() => navigate({ screen: 'reviews', recordId: null })}
        onRelogin={onRelogin}
      />
    )
  return (
    <ReviewList api={api} onOpen={(id) => navigate({ screen: 'reviews', recordId: id })} onRelogin={onRelogin} />
  )
}

const REVIEW_COLUMNS = 'md:grid-cols-[6.5rem_minmax(0,2fr)_6rem_minmax(0,2.5fr)_9.5rem_7rem]'

/** 審査待ちの一覧。開くたびに 1 ページ目から読み込む */
function ReviewList({
  api,
  onOpen,
  onRelogin,
}: {
  api: ApiClient
  onOpen: (recordId: string) => void
  onRelogin: () => void
}) {
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    api.listReviews(token).then(
      (result) => {
        if (active) setList({ kind: 'ready', page: result })
      },
      (failure: unknown) => {
        if (active)
          setList({
            kind: 'error',
            error: toApiError(failure, '審査待ちの一覧を取得できませんでした。'),
          })
      },
    )
    return () => {
      active = false
    }
  }, [api, token, revision])

  function reload() {
    setList({ kind: 'loading' })
    setToken(undefined)
    setRevision((value) => value + 1)
  }

  return (
    <section aria-labelledby="reviews-heading">
      <PageHeader
        headingId="reviews-heading"
        title="審査待ち"
        description={
          <>
            <p>
              AWS の承認待ち（PENDING_APPROVAL）を正本として表示します。申請のスナップショットを確認してから
              公開承認または却下を行ってください。公開承認は実行の許可ではありません。
            </p>
            <p>
              未解決の廃止依頼は、最初のページに古い順で表示します。公開停止で止めてから、廃止するか見送るかを判断できます。
            </p>
          </>
        }
      />
      {list.kind === 'loading' ? (
        <LoadingState>一覧を読み込んでいます…</LoadingState>
      ) : list.kind === 'error' ? (
        <RequestErrorAlert error={list.error} onRelogin={onRelogin}>
          <button type="button" className={button.dangerOutline} onClick={reload}>
            一覧を再読み込み
          </button>
        </RequestErrorAlert>
      ) : (
        <div className="space-y-4">
          {list.page.items.length === 0 && (
            <EmptyState hint={list.page.nextToken ? undefined : '審査が必要な申請と廃止依頼はすべて処理済みです。'}>
              {list.page.nextToken ? 'このページに審査待ちはありません。' : '審査待ちはありません。'}
            </EmptyState>
          )}
          {list.page.items.length > 0 && (
            <div className={`${card} overflow-hidden`}>
              <div
                className={`hidden md:grid ${REVIEW_COLUMNS} bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600`}
                aria-hidden="true"
              >
                <span className="py-3 px-4">区分</span>
                <span className="py-3 px-4">レコード名 / 所有チーム</span>
                <span className="py-3 px-4">版</span>
                <span className="py-3 px-4">理由・申請者 / 経過</span>
                <span className="py-3 px-4">状態</span>
                <span className="py-3 px-4 text-right">操作</span>
              </div>
              <ul className="divide-y divide-slate-200 text-sm">
                {list.page.items.map((item) =>
                  item.reviewKind === 'DEPRECATION_REQUEST' ? (
                    <li key={`request-${item.recordId}`} className="hover:bg-slate-50/70">
                      <div className={`grid grid-cols-1 ${REVIEW_COLUMNS} items-start`}>
                        <p className="pt-3.5 px-4 md:py-3.5">
                          <span className={badge.danger}>廃止依頼</span>
                        </p>
                        <ReviewRecordCell item={item} />
                        <p className="px-4 md:py-3.5 text-xs font-mono font-semibold text-slate-900">版 {item.recordVersion}</p>
                        <div className="px-4 md:py-3.5 text-xs text-slate-600 break-all">
                          <p>
                            廃止依頼: {item.reason}（依頼者 {item.requestedBy.subject}）
                          </p>
                          <p className="mt-0.5 text-rose-700 font-medium">{requestProgress(item)}</p>
                        </div>
                        <p className="px-4 md:py-3.5 text-xs">
                          <span className={badge.neutral}>
                            状態 {recordStatusLabel(item.status)}
                            {item.operationId && ' / 結果確認中'}
                          </span>
                        </p>
                        <div className="py-3.5 px-4 md:text-right" />
                      </div>
                      <div className="px-4 pb-4 space-y-2">
                        {!item.managed && (
                          <p className={callout.warning}>ポータルで管理されていないため、ここでは対応できません。</p>
                        )}
                        <RecordPublicationToggle
                          api={api}
                          recordId={item.recordId}
                          onRelogin={onRelogin}
                          label="対応する"
                        />
                      </div>
                    </li>
                  ) : (
                    <li key={item.recordId} className="hover:bg-slate-50/70">
                      <div className={`grid grid-cols-1 ${REVIEW_COLUMNS} items-start`}>
                        <p className="pt-3.5 px-4 md:py-3.5">
                          <span className={badge.warning}>申請</span>
                        </p>
                        <ReviewRecordCell item={item} />
                        <p className="px-4 md:py-3.5 text-xs font-mono font-semibold text-slate-900">版 {item.recordVersion}</p>
                        <div className="px-4 md:py-3.5 text-xs text-slate-600 break-all">
                          <p>
                            申請理由: {submissionLine(item)}
                            {item.submittedBy && `（申請者 ${item.submittedBy.subject}）`}
                          </p>
                        </div>
                        <p className="px-4 md:py-3.5 text-xs">
                          <span className={badge.warning}>
                            状態 {recordStatusLabel(item.status)}
                            {item.operationId && ' / 結果確認中'}
                          </span>
                        </p>
                        <div className="py-3.5 px-4 md:text-right">
                          <button
                            type="button"
                            className={button.smallPrimary}
                            disabled={item.operationId !== null || !item.snapshotHash}
                            onClick={() => onOpen(item.recordId)}
                          >
                            審査する
                          </button>
                        </div>
                      </div>
                      {!item.managed && (
                        <p className={`mx-4 mb-4 ${callout.warning}`}>ポータルで管理されていないため、ここでは決裁できません。</p>
                      )}
                    </li>
                  ),
                )}
              </ul>
            </div>
          )}
          <nav className={pager} aria-label="審査待ちのページ">
            <button
              type="button"
              className={button.small}
              disabled={token === undefined}
              onClick={() => {
                setList({ kind: 'loading' })
                setToken(undefined)
              }}
            >
              最初のページ
            </button>
            <button
              type="button"
              className={button.small}
              disabled={list.page.nextToken === null}
              onClick={() => {
                if (list.page.nextToken) {
                  setToken(list.page.nextToken)
                  setList({ kind: 'loading' })
                }
              }}
            >
              次のページ
            </button>
          </nav>
        </div>
      )}
    </section>
  )
}

/** 一覧の名前・種別・所有チームの列 */
function ReviewRecordCell({ item }: { item: SubmissionSummary | DeprecationRequestSummary }) {
  return (
    <div className="px-4 md:py-3.5 min-w-0">
      <h3 className="font-semibold text-slate-900 break-all">{item.displayName}</h3>
      <p className="text-xs text-slate-500 break-all">
        {RECORD_TYPE_LABELS[item.recordType]}・<span className="font-mono">{item.name}</span>・
        <strong>{item.ownerTeam ?? '所有チーム不明'}</strong>
      </p>
    </div>
  )
}

type LoaderState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: PendingDetail }
  | { kind: 'error'; error: ApiError }

/** #/reviews/<レコード ID> の審査対象を取得してから、審査の画面を出す */
function ReviewDetailLoader({
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
      <ReviewBreadcrumb onBack={onBack} />
      {state.kind === 'loading' ? (
        <LoadingState>審査対象を読み込んでいます…</LoadingState>
      ) : (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={() => setState({ kind: 'loading' })} />
      )}
    </section>
  )
}

/** 審査詳細の上部。一覧への戻り道 */
function ReviewBreadcrumb({ onBack, backDisabled = false, children }: { onBack: () => void; backDisabled?: boolean; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <nav aria-label="パンくずリスト" className="flex items-center gap-2 text-xs text-slate-500">
        <RouteLink to={{ screen: 'reviews', recordId: null }} className="hover:text-slate-900">
          審査待ち
        </RouteLink>
        <span aria-hidden="true">/</span>
        <span className="text-slate-900 font-medium">審査詳細</span>
      </nav>
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <button type="button" className={button.small} disabled={backDisabled} onClick={onBack}>
          一覧に戻る
        </button>
      </div>
    </div>
  )
}

type DecideState =
  | { kind: 'idle' }
  | { kind: 'deciding' }
  /** retry は、同じ Idempotency-Key で送り直せる決裁の種別。再取得の失敗など決裁以外の失敗では null */
  | { kind: 'error'; error: ApiError; retry: DecisionKind | null }
  | { kind: 'result'; operation: DecisionOperation }

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
      const error = toApiError(failure, DECISION_COPY[kind].failure)
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
      <ReviewBreadcrumb onBack={onBack} backDisabled={deciding}>
        <div className="w-56">
          <RecordHistoryToggle api={api} recordId={detail.recordId} onRelogin={onRelogin} />
        </div>
      </ReviewBreadcrumb>
      <section className={`${card} p-6`}>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className={badge.warning}>{recordStatusLabel(detail.status)}</span>
          <span className={badge.neutral}>{RECORD_TYPE_LABELS[detail.recordType]}</span>
        </div>
        <h1 id="review-heading" className="text-2xl font-bold text-slate-900 break-all">
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
                  className={`rounded-xl border p-4 ${DECISION_TONES[kind].frame}`}
                  onSubmit={(event) => {
                    event.preventDefault()
                    void decide(kind)
                  }}
                >
                  <fieldset disabled={locked} className="space-y-3">
                    <legend className="text-sm font-semibold text-slate-900">{DECISION_COPY[kind].legend}</legend>
                    <label className={fieldLabel}>
                      {DECISION_COPY[kind].reasonLabel}
                      <textarea
                        name={DECISION_COPY[kind].field}
                        required
                        maxLength={STATUS_REASON_MAX_LENGTH}
                        rows={3}
                        value={reasons[kind]}
                        onChange={(event) =>
                          setReasons((current) => ({ ...current, [kind]: event.target.value }))
                        }
                      />
                    </label>
                    <button type="submit" className={DECISION_TONES[kind].button} disabled={locked || !reasons[kind].trim()}>
                      {DECISION_COPY[kind].submit}
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

/** 決裁できない申請の警告（左に太線のある赤い囲み） */
const DECISION_BLOCKED = 'bg-rose-50 border-l-4 border-rose-600 rounded-r-xl p-4 text-sm text-rose-950'

/** 公開承認は緑、却下は赤で見分ける */
const DECISION_TONES: Record<DecisionKind, { frame: string; button: string }> = {
  approve: {
    frame: 'border-emerald-200 bg-emerald-50/30',
    button:
      'inline-flex items-center justify-center px-5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold shadow-xs disabled:opacity-50 disabled:cursor-not-allowed',
  },
  reject: {
    frame: 'border-rose-200 bg-rose-50/30',
    button:
      'inline-flex items-center justify-center px-5 py-2 rounded-lg border border-rose-300 bg-rose-50 hover:bg-rose-100 text-rose-800 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed',
  },
}

function SnapshotSection({ snapshot }: { snapshot: SubmissionSnapshot }) {
  return (
    <section aria-labelledby="review-snapshot-heading" className={`${card} p-6 space-y-3`}>
      <h2 id="review-snapshot-heading" className="text-base font-semibold text-slate-900">
        申請のスナップショット
      </h2>
      <p className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-lg p-3 break-all">
        <strong className="font-semibold text-slate-900">申請理由: </strong>
        {snapshot.reason}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs text-slate-700">
        <p className="break-all">申請者: {snapshot.submittedBy.subject} / 最終編集者: {snapshot.lastEditedBy.subject}</p>
        <p className="break-all">連絡先: {snapshot.contact || '未登録'} / 利用条件: {snapshot.termsOfUse || '未登録'}</p>
        <p className="break-all">接続先の参照: <span className="font-mono">{snapshot.connectionReference || '未登録'}</span></p>
        <p className="break-all">関連参照: <span className="font-mono">{formatReferences(snapshot.relatedReferences)}</span></p>
      </div>
      <details className="pt-1">
        <summary className="text-xs font-semibold text-slate-700 select-none">申請された定義と metadata を確認</summary>
        <pre className="mt-3 p-4 rounded-lg bg-slate-900 text-slate-100 text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all">
          {JSON.stringify({ record: snapshot.record, supplement: snapshot.supplement }, null, 2)}
        </pre>
      </details>
    </section>
  )
}

/** 差分で比べる項目。公開中の版（スナップショットがない項目は null）と申請された版 */
type DiffFields = {
  recordVersion: string
  contact: string | null
  termsOfUse: string | null
  connectionReference: string | null
  relatedReferences: RecordReference[]
  record: Record<string, unknown> | null
}

/** 定義と metadata のうち、差分で比べる項目（AWS のレコードの項目名と表示名） */
const RECORD_DIFF_FIELDS: [string, string][] = [
  ['displayName', '表示名'],
  ['description', '説明'],
  ['descriptors', '定義（descriptors）'],
  ['customMetadata', 'metadata'],
]

function recordValue(record: Record<string, unknown> | null, key: string): string {
  if (record === null) return '未取得'
  const value = record[key]
  if (value === undefined || value === null || value === '') return '未登録'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** 公開中の版と申請のスナップショットを比べる。ハッシュで対象を固定するため、差分は参考情報 */
function DiffTable({ next, published }: { next: DiffFields; published: PublishedBase }) {
  const rows: [string, string, string][] = [
    ['版', published.recordVersion, next.recordVersion],
    ...RECORD_DIFF_FIELDS.map(([key, label]): [string, string, string] => [
      label,
      recordValue(published.record, key),
      recordValue(next.record, key),
    ]),
    ['連絡先', published.contact ?? '未登録', next.contact || '未登録'],
    ['利用条件', published.termsOfUse ?? '未登録', next.termsOfUse || '未登録'],
    ['接続先の参照', published.connectionReference ?? '未登録', next.connectionReference || '未登録'],
    ['関連参照', formatReferences(published.relatedReferences), formatReferences(next.relatedReferences)],
  ]
  return (
    <div className="overflow-x-auto">
      <table className="diff-table text-xs">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
            <th className="py-3 px-4 w-36 text-left">項目</th>
            <th className="py-3 px-4 text-left">公開中の版</th>
            <th className="py-3 px-4 text-left">申請された版</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {rows.map(([label, before, after]) => {
            const changed = before !== after
            return (
              <tr key={label} className={changed ? 'bg-amber-50/40' : undefined}>
                <th className="py-3 px-4 font-semibold text-slate-700 text-left">{label}</th>
                <td className="py-3 px-4 text-slate-600">{before}</td>
                <td className={changed ? 'py-3 px-4 font-medium text-slate-900' : 'py-3 px-4 text-slate-700'}>{after}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
