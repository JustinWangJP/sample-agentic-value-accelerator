import { useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RecoveredOperationNotice } from '../components/RecoveryDetails'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import {
  PENDING_APPROVAL_STATUS,
  PUBLISHED_STATUS,
  isUncertainWriteError,
  settlesIdempotencyKey,
  type RecordStatus,
} from '../drafts/api'
import {
  DECISION_REASON_MAX_LENGTH,
  type DecisionKind,
  type DecisionOperation,
  type PendingDetail,
  type PendingPage,
  type PendingSummary,
  type Principal,
  type PublishedBase,
  type RecordReference,
  type SubmissionSnapshot,
} from './api'

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

/** 画面に出す AWS の状態。PENDING_APPROVAL は「審査待ち」と表示する（GLOSSARY「申請」） */
function statusLabel(status: RecordStatus): string {
  return status === PENDING_APPROVAL_STATUS ? '審査待ち' : status
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
function submissionLine(item: PendingSummary): string {
  if (item.submissionState === 'MISSING')
    return '申請スナップショットを取得できません（管理者に連絡してください）'
  if (item.submissionState === 'NONE' || item.reason === null) return 'ポータル経由の申請なし'
  return item.reason
}

/** Curator の審査待ちの一覧と、申請のスナップショットに基づく公開承認・却下 */
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
                    廃止依頼: {item.reason ?? '（理由なし）'}（依頼者{' '}
                    {item.requestedBy ? item.requestedBy.subject : '不明'}）
                  </p>
                  <p>状態 {statusLabel(item.status)}</p>
                  {!item.managed && <p>ポータルで管理されていないため、ここでは対応できません。</p>}
                  <DeprecationRequestActions
                    api={api}
                    item={item}
                    onRelogin={onRelogin}
                    onDone={back}
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
                    状態 {statusLabel(item.status)}
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
                    maxLength={DECISION_REASON_MAX_LENGTH}
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

function DecisionOperationCheck({
  api,
  operationId,
  onDone,
  onRelogin,
}: {
  api: ApiClient
  operationId: string
  onDone: (operation: DecisionOperation) => void
  onRelogin: () => void
}) {
  const [message, setMessage] = useState('決裁の結果確認中です。確定するまでこのレコードへの書き込みを停止しています。')
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  // Admin の回復操作で確定した決裁は、回復結果を見せてから結果の表示へ進む
  const [recovered, setRecovered] = useState<DecisionOperation | null>(null)
  async function check() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.getDecisionOperation(operationId)
      if (result.operationState === 'VERIFYING') setMessage(result.message)
      else if (result.recovery) setRecovered(result)
      else onDone(result)
    } catch (failure) {
      setError(toApiError(failure, '決裁の結果を取得できませんでした。'))
    } finally {
      setBusy(false)
    }
  }
  if (recovered?.recovery) {
    return (
      <RecoveredOperationNotice
        recovery={recovered.recovery}
        onContinue={() => onDone(recovered)}
      />
    )
  }
  return (
    <div>
      <p role="status">{message}</p>
      <button type="button" disabled={busy} onClick={() => void check()}>
        決裁の結果を確認
      </button>
      {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
    </div>
  )
}

type LifecycleState =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'result'; operation: DecisionOperation }

/**
 * 廃止依頼への対応。Curator が公開停止（却下による非公開化）または廃止（元に戻せない）を行う。
 * 廃止は理由の入力に加えて、確認のチェック（二重確認）を必須にする（設計書「業務上の状態」）
 */
function DeprecationRequestActions({
  api,
  item,
  onRelogin,
  onDone,
}: {
  api: ApiClient
  item: PendingSummary
  onRelogin: () => void
  onDone: () => void
}) {
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [state, setState] = useState<LifecycleState>({ kind: 'idle' })
  const busy = state.kind === 'busy'

  async function run(kind: 'suspend' | 'deprecate') {
    const trimmed = reason.trim()
    if (!trimmed) return
    if (kind === 'deprecate' && !confirmed) return
    setState({ kind: 'busy' })
    try {
      const operation =
        kind === 'suspend'
          ? await api.suspendRecord(item.recordId, { reason: trimmed }, crypto.randomUUID())
          : await api.deprecateRecord(
              item.recordId,
              { reason: trimmed, confirm: true },
              crypto.randomUUID(),
            )
      setState({ kind: 'result', operation })
    } catch (failure) {
      setState({
        kind: 'error',
        error: toApiError(
          failure,
          kind === 'suspend' ? '公開停止できませんでした。' : '廃止できませんでした。',
        ),
      })
    }
  }

  return (
    <section aria-label={`${item.displayName} の廃止依頼への対応`}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void run('suspend')
        }}
      >
        <fieldset disabled={busy || !item.managed}>
          <legend>公開停止</legend>
          <p>
            問題が見つかったものを理由付きで発見できなくします。これは却下による非公開化のため、
            再公開には Publisher による編集と再申請が必要です。
          </p>
          <label>
            公開停止の理由（必須）
            <textarea
              name="suspendReason"
              required
              maxLength={DECISION_REASON_MAX_LENGTH}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <button type="submit" disabled={busy || !reason.trim()}>
            公開停止する
          </button>
        </fieldset>
      </form>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void run('deprecate')
        }}
      >
        <fieldset disabled={busy || !item.managed}>
          <legend>廃止</legend>
          <p>廃止は元に戻せません。レコードは DEPRECATED になり、再公開には新規の登録が必要です。</p>
          <label>
            廃止理由（必須）
            <textarea
              name="deprecateReason"
              required
              maxLength={DECISION_REASON_MAX_LENGTH}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              name="deprecateConfirm"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            廃止は元に戻せないことを確認しました
          </label>
          <button type="submit" disabled={busy || !reason.trim() || !confirmed}>
            廃止する
          </button>
        </fieldset>
      </form>
      {state.kind === 'busy' && <p role="status">実行しています…</p>}
      {state.kind === 'error' && (
        <>
          <RequestErrorAlert error={state.error} onRelogin={onRelogin} />
          {state.error.status === 409 && (
            <p>レコードの状態が変わっています。一覧を再読み込みして最新の状態を確認してください。</p>
          )}
        </>
      )}
      {state.kind === 'result' && state.operation.operationState === 'SUCCEEDED' && (
        <p role="status">
          {state.operation.status === 'DEPRECATED'
            ? '廃止しました。この操作は元に戻せません。'
            : '公開停止しました。再公開には Publisher による編集と再申請が必要です。'}
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
      {state.kind === 'idle' && item.status === 'DEPRECATED' && (
        <p>このレコードはすでに廃止されています。一覧を再読み込みしてください。</p>
      )}
      <button type="button" onClick={onDone} disabled={busy}>
        一覧を再読み込み
      </button>
    </section>
  )
}
