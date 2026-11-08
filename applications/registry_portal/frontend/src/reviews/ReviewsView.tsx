import { useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { isUncertainWriteError, settlesIdempotencyKey } from '../drafts/api'
import {
  REVIEW_REASON_MAX_LENGTH,
  type DecisionOperation,
  type ReviewDetail,
  type ReviewPage,
  type ReviewReference,
} from './api'

type Props = { api: ApiClient; roles: string[]; onRelogin: () => void }
type ListState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: ReviewPage }
  | { kind: 'error'; error: ApiError }

/** Curator の審査待ちの一覧と、申請のスナップショットに基づく公開承認・却下 */
export function ReviewsView({ api, roles, onRelogin }: Props) {
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)
  const [detail, setDetail] = useState<ReviewDetail | null>(null)
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
            {list.page.items.map((item) => (
              <li key={item.recordId}>
                <h3>{item.displayName}</h3>
                <p>
                  {RECORD_TYPE_LABELS[item.recordType]}・{item.name}・
                  {item.ownerTeam ?? '所有チーム不明'}・版 {item.recordVersion}
                </p>
                <p>
                  申請理由: {item.reason ?? 'ポータル経由の申請なし'}
                  {item.submittedBy && `（申請者 ${item.submittedBy.subject}）`}
                </p>
                <p>
                  AWS の状態 {item.status}
                  {item.operationId && ' / 結果確認中'}
                </p>
                {!item.managed && <p>ポータルで管理されていないため、ここでは決裁できません。</p>}
                <button
                  type="button"
                  disabled={item.operationId !== null || !item.snapshotHash}
                  onClick={() => void openDetail(item.recordId)}
                >
                  審査する
                </button>
              </li>
            ))}
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
  | { kind: 'error'; error: ApiError; approved: boolean }
  | { kind: 'result'; operation: DecisionOperation }

function ReviewDetailView({
  api,
  initial,
  onBack,
  onRelogin,
}: {
  api: ApiClient
  initial: ReviewDetail
  onBack: () => void
  onRelogin: () => void
}) {
  const [detail, setDetail] = useState(initial)
  const [approveReason, setApproveReason] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [approveKey, setApproveKey] = useState(() => crypto.randomUUID())
  const [rejectKey, setRejectKey] = useState(() => crypto.randomUUID())
  const [state, setState] = useState<DecideState>({ kind: 'idle' })
  const snapshot = detail.snapshot
  const uncertain = state.kind === 'error' && isUncertainWriteError(state.error)
  const decided =
    state.kind === 'result' && state.operation.operationState === 'SUCCEEDED'
  const deciding = state.kind === 'deciding'
  const locked = deciding || decided

  async function decide(approved: boolean, retry = false) {
    if (!snapshot) return
    const reason = approved ? approveReason.trim() : rejectReason.trim()
    if (!approved && !reason) return
    if (locked && !retry) return
    setState({ kind: 'deciding' })
    try {
      const input = { snapshotHash: snapshot.snapshotHash, reason }
      const operation = approved
        ? await api.approveReview(detail.recordId, input, approveKey)
        : await api.rejectReview(detail.recordId, input, rejectKey)
      if (operation.operationState === 'FAILED') {
        if (approved) setApproveKey(crypto.randomUUID())
        else setRejectKey(crypto.randomUUID())
      }
      setState({ kind: 'result', operation })
    } catch (failure) {
      const error = toApiError(failure, approved ? '公開承認できませんでした。' : '却下できませんでした。')
      if (settlesIdempotencyKey(error)) {
        if (approved) setApproveKey(crypto.randomUUID())
        else setRejectKey(crypto.randomUUID())
      }
      setState({ kind: 'error', error, approved })
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
        approved: true,
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
          このレコードにはポータル経由の申請がないため、決裁できません。管理者に連絡してください。
        </p>
      ) : (
        <>
          {detail.invalidated && (
            <p role="alert">
              この申請は審査中の編集で無効になっています。決裁できません。一覧で最新の状態を確認してください。
            </p>
          )}
          <section aria-labelledby="review-snapshot-heading">
            <h4 id="review-snapshot-heading">申請のスナップショット</h4>
            <p>申請理由: {snapshot.reason}</p>
            <p>申請者: {snapshot.submittedBy.subject} / 最終編集者: {snapshot.lastEditedBy.subject}</p>
            <p>連絡先: {snapshot.contact || '未登録'} / 利用条件: {snapshot.termsOfUse || '未登録'}</p>
            <p>接続先の参照: {snapshot.connectionReference || '未登録'}</p>
            <p>
              関連参照:{' '}
              {snapshot.relatedReferences.length > 0
                ? snapshot.relatedReferences
                    .map((reference) => `${reference.recordId} @ ${reference.recordVersion}`)
                    .join('、')
                : 'ありません'}
            </p>
            <details>
              <summary>申請された定義と metadata を確認</summary>
              <pre>{JSON.stringify({ record: snapshot.record, supplement: snapshot.supplement }, null, 2)}</pre>
            </details>
          </section>
          <section aria-labelledby="review-diff-heading">
            <h4 id="review-diff-heading">公開中の版との差分</h4>
            {detail.published ? (
              <DiffTable
                next={{
                  recordVersion: detail.recordVersion,
                  contact: snapshot.contact,
                  termsOfUse: snapshot.termsOfUse,
                  connectionReference: snapshot.connectionReference,
                  relatedReferences: snapshot.relatedReferences,
                }}
                published={detail.published}
              />
            ) : (
              <p>公開版がないため、すべての項目が初めて公開されます。</p>
            )}
          </section>
          <form onSubmit={(event) => { event.preventDefault(); void decide(true) }}>
            <fieldset disabled={locked}>
              <legend>公開承認</legend>
              <label>
                承認理由（判断の根拠として記録します）
                <textarea
                  name="approveReason"
                  maxLength={REVIEW_REASON_MAX_LENGTH}
                  rows={3}
                  value={approveReason}
                  onChange={(event) => setApproveReason(event.target.value)}
                />
              </label>
              <button type="submit" disabled={locked}>
                公開承認する
              </button>
            </fieldset>
          </form>
          <form onSubmit={(event) => { event.preventDefault(); void decide(false) }}>
            <fieldset disabled={locked}>
              <legend>却下</legend>
              <label>
                却下理由（必須。Publisher が修正の参考にします）
                <textarea
                  name="rejectReason"
                  required
                  maxLength={REVIEW_REASON_MAX_LENGTH}
                  rows={3}
                  value={rejectReason}
                  onChange={(event) => setRejectReason(event.target.value)}
                />
              </label>
              <button type="submit" disabled={locked || !rejectReason.trim()}>
                却下する
              </button>
            </fieldset>
          </form>
        </>
      )}
      {state.kind === 'deciding' && <p role="status">決裁しています…</p>}
      {state.kind === 'error' && (
        <>
          <RequestErrorAlert error={state.error} onRelogin={onRelogin} />
          {state.error.status === 409 && (
            <p>審査した内容が現在の申請と一致しません。最新の申請を読み込んでから決裁してください。</p>
          )}
          {uncertain && (
            <p>
              決裁結果が不明です。同じ内容で再試行すると、同じ操作として確定を試みます。
              <button
                type="button"
                disabled={deciding}
                onClick={() => void decide(state.approved, true)}
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
          {state.operation.status === 'APPROVED'
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
      <button type="button" disabled={deciding} onClick={onBack}>
        一覧に戻る
      </button>
    </section>
  )
}

/** 差分で比べる項目。公開中の版（スナップショットがない項目は null）と申請された版 */
type DiffFields = {
  recordVersion: string
  contact: string | null
  termsOfUse: string | null
  connectionReference: string | null
  relatedReferences: ReviewReference[]
}

/** 公開中の版と申請のスナップショットを比べる。ハッシュで対象を固定するため、差分は参考情報 */
function DiffTable({ next, published }: { next: DiffFields; published: DiffFields }) {
  const rows: [string, string, string][] = [
    ['版', published.recordVersion, next.recordVersion],
    ['連絡先', published.contact ?? '未登録', next.contact || '未登録'],
    ['利用条件', published.termsOfUse ?? '未登録', next.termsOfUse || '未登録'],
    ['接続先の参照', published.connectionReference ?? '未登録', next.connectionReference || '未登録'],
    [
      '関連参照',
      published.relatedReferences.map((r) => `${r.recordId} @ ${r.recordVersion}`).join('、') || 'ありません',
      next.relatedReferences.map((r) => `${r.recordId} @ ${r.recordVersion}`).join('、') || 'ありません',
    ],
  ]
  return (
    <table>
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
  async function check() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.getDecisionOperation(operationId)
      if (result.operationState !== 'VERIFYING') onDone(result)
      else setMessage(result.message)
    } catch (failure) {
      setError(toApiError(failure, '決裁の結果を取得できませんでした。'))
    } finally {
      setBusy(false)
    }
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
