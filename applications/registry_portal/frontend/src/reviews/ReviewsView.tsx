import { useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import {
  PUBLISHED_STATUS,
  STATUS_REASON_MAX_LENGTH,
  isUncertainWriteError,
  recordStatusLabel,
  settlesIdempotencyKey,
} from '../drafts/api'
import { elapsedDays } from '../lifecycle/api'
import { RecordLifecycleToggle } from '../lifecycle/RecordLifecyclePanel'
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
import { DecisionOperationCheck } from './DecisionOperationCheck'

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
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)
  const [detail, setDetail] = useState<PendingDetail | null>(null)
  const isCurator = roles.includes('Curator')

  useEffect(() => {
    if (!isCurator || detail) return
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
  }, [api, isCurator, detail, token, revision])

  if (!isCurator) return null

  function back() {
    setDetail(null)
    setList({ kind: 'loading' })
    setToken(undefined)
    setRevision((value) => value + 1)
  }

  if (detail)
    return (
      <ReviewDetailView api={api} initial={detail} onBack={back} onRelogin={onRelogin} />
    )

  return (
    <section className="reviews" aria-labelledby="reviews-heading">
      <h2 id="reviews-heading">審査待ち</h2>
      <p>
        AWS の承認待ち（PENDING_APPROVAL）を正本として表示します。申請のスナップショットを確認してから
        公開承認または却下を行ってください。公開承認は実行の許可ではありません。
      </p>
      <p>
        未解決の廃止依頼は、最初のページに古い順で表示します。公開停止で止めてから、廃止するか見送るかを判断できます。
      </p>
      {list.kind === 'loading' ? (
        <p role="status">一覧を読み込んでいます…</p>
      ) : list.kind === 'error' ? (
        <>
          <RequestErrorAlert error={list.error} onRelogin={onRelogin} />
          <button type="button" onClick={back}>
            一覧を再読み込み
          </button>
        </>
      ) : (
        <>
          {list.page.items.length === 0 && (
            <p>{list.page.nextToken ? 'このページに審査待ちはありません。' : '審査待ちはありません。'}</p>
          )}
          <ul className="catalog-items">
            {list.page.items.map((item) =>
              item.reviewKind === 'DEPRECATION_REQUEST' ? (
                <li key={`request-${item.recordId}`}>
                  <h3>{item.displayName}</h3>
                  <p>
                    {RECORD_TYPE_LABELS[item.recordType]}・{item.name}・
                    {item.ownerTeam ?? '所有チーム不明'}・版 {item.recordVersion}
                  </p>
                  <p>
                    廃止依頼: {item.reason}（依頼者 {item.requestedBy.subject}）
                  </p>
                  <p>
                    状態 {recordStatusLabel(item.status)} / {requestProgress(item)}
                    {item.operationId && ' / 結果確認中'}
                  </p>
                  {!item.managed && <p>ポータルで管理されていないため、ここでは対応できません。</p>}
                  <RecordLifecycleToggle
                    api={api}
                    recordId={item.recordId}
                    onRelogin={onRelogin}
                    label="対応する"
                  />
                </li>
              ) : (
                <li key={item.recordId}>
                  <h3>{item.displayName}</h3>
                  <p>
                    {RECORD_TYPE_LABELS[item.recordType]}・{item.name}・
                    {item.ownerTeam ?? '所有チーム不明'}・版 {item.recordVersion}
                  </p>
                  <p>
                    申請理由: {submissionLine(item)}
                    {item.submittedBy && `（申請者 ${item.submittedBy.subject}）`}
                  </p>
                  <p>
                    状態 {recordStatusLabel(item.status)}
                    {item.operationId && ' / 結果確認中'}
                  </p>
                  {!item.managed && (
                    <p>ポータルで管理されていないため、ここでは決裁できません。</p>
                  )}
                  <button
                    type="button"
                    disabled={item.operationId !== null || !item.snapshotHash}
                    onClick={() => void openDetail(item.recordId)}
                  >
                    審査する
                  </button>
                </li>
              ),
            )}
          </ul>
          <nav className="pager" aria-label="審査待ちのページ">
            <button
              type="button"
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
        </>
      )}
    </section>
  )

  async function openDetail(recordId: string) {
    try {
      setDetail(await api.getReview(recordId))
    } catch (failure) {
      setList({ kind: 'error', error: toApiError(failure, '審査対象を取得できませんでした。') })
    }
  }
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
    <section aria-labelledby="review-heading">
      <h3 id="review-heading">審査する</h3>
      <p>
        {detail.displayName}（{RECORD_TYPE_LABELS[detail.recordType]}）・版 {detail.recordVersion}
      </p>
      <p>
        所有チーム {detail.ownerTeam ?? '不明'} /{' '}
        {detail.publishedVersion ? `公開中の版 ${detail.publishedVersion}` : '公開版なし（初回公開）'}
      </p>
      {detail.deprecationRequest && (
        <p role="alert">
          このレコードには未解決の廃止依頼があります（理由: {detail.deprecationRequest.reason}・依頼者{' '}
          {detail.deprecationRequest.requestedBy.subject}）。依頼者の意図を確認してから決裁してください。
        </p>
      )}
      {!snapshot ? (
        <p role="alert">
          {detail.submissionState === 'MISSING'
            ? '申請スナップショットを取得できないため、決裁できません。管理者に連絡してください。'
            : 'このレコードにはポータル経由の申請がないため、決裁できません。管理者に連絡してください。'}
        </p>
      ) : (
        <>
          {detail.invalidated && (
            <p role="alert">
              この申請は審査中の編集で無効になっています。決裁できません。一覧で最新の状態を確認してください。
            </p>
          )}
          {!detail.snapshotIntact && (
            <p role="alert">
              保存された申請スナップショットが申請時の内容と一致しません。決裁できません。Publisher
              に再申請を依頼し、管理者に連絡してください。
            </p>
          )}
          <SnapshotSection snapshot={snapshot} />
          <section aria-labelledby="review-diff-heading">
            <h4 id="review-diff-heading">公開中の版との差分</h4>
            {detail.publishedState === 'MISSING' ? (
              <p role="alert">
                公開中の版 {detail.publishedVersion} のスナップショットは未取得です。差分を確認できないため、
                管理者に連絡してください。
              </p>
            ) : detail.published ? (
              <>
                <p>公開承認した Curator: {principalLabel(detail.published.approvedBy)}</p>
                <DiffTable
                  next={{ ...snapshot, recordVersion: detail.recordVersion }}
                  published={detail.published}
                />
              </>
            ) : (
              <p>公開版がないため、すべての項目が初めて公開されます。</p>
            )}
          </section>
          {(['approve', 'reject'] as const).map((kind) => (
            <form
              key={kind}
              onSubmit={(event) => {
                event.preventDefault()
                void decide(kind)
              }}
            >
              <fieldset disabled={locked}>
                <legend>{DECISION_COPY[kind].legend}</legend>
                <label>
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
                <button type="submit" disabled={locked || !reasons[kind].trim()}>
                  {DECISION_COPY[kind].submit}
                </button>
              </fieldset>
            </form>
          ))}
        </>
      )}
      {state.kind === 'deciding' && <p role="status">決裁しています…</p>}
      {state.kind === 'error' && (
        <>
          <RequestErrorAlert error={state.error} onRelogin={onRelogin} />
          {state.error.status === 409 && (
            <p>審査した内容が現在の申請と一致しません。最新の申請を読み込んでから決裁してください。</p>
          )}
          {uncertain && state.retry !== null && (
            <p>
              決裁結果が不明です。同じ内容で再試行すると、同じ操作として確定を試みます。
              <button
                type="button"
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
            <button type="button" onClick={() => void reload()}>
              最新の申請を再読み込み
            </button>
          )}
        </>
      )}
      {state.kind === 'result' && state.operation.operationState === 'SUCCEEDED' && (
        <p role="status">
          {state.operation.status === PUBLISHED_STATUS
            ? `公開承認しました（版 ${state.operation.recordVersion}）。カタログ検索への反映は数分かかることがあります（検索への反映待ち）。`
            : '却下しました。Publisher が編集して再申請します。'}
        </p>
      )}
      {state.kind === 'result' && state.operation.operationState === 'VERIFYING' && (
        <DecisionOperationCheck
          api={api}
          operationId={state.operation.operationId}
          onDone={(operation) => setState({ kind: 'result', operation })}
          onRelogin={onRelogin}
        />
      )}
      {state.kind === 'result' && state.operation.operationState === 'FAILED' && (
        <p role="alert">{state.operation.message}</p>
      )}
      <RecordHistoryToggle api={api} recordId={detail.recordId} onRelogin={onRelogin} />
      <button type="button" disabled={deciding} onClick={onBack}>
        一覧に戻る
      </button>
    </section>
  )
}

function SnapshotSection({ snapshot }: { snapshot: SubmissionSnapshot }) {
  return (
    <section aria-labelledby="review-snapshot-heading">
      <h4 id="review-snapshot-heading">申請のスナップショット</h4>
      <p>申請理由: {snapshot.reason}</p>
      <p>申請者: {snapshot.submittedBy.subject} / 最終編集者: {snapshot.lastEditedBy.subject}</p>
      <p>連絡先: {snapshot.contact || '未登録'} / 利用条件: {snapshot.termsOfUse || '未登録'}</p>
      <p>接続先の参照: {snapshot.connectionReference || '未登録'}</p>
      <p>関連参照: {formatReferences(snapshot.relatedReferences)}</p>
      <details>
        <summary>申請された定義と metadata を確認</summary>
        <pre>{JSON.stringify({ record: snapshot.record, supplement: snapshot.supplement }, null, 2)}</pre>
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
    <table className="review-diff">
      <thead>
        <tr>
          <th>項目</th>
          <th>公開中の版</th>
          <th>申請された版</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, before, after]) => (
          <tr key={label}>
            <th>{label}</th>
            <td>{before}</td>
            <td>{after}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
