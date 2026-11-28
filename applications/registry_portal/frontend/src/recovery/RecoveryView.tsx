import { useState, type FormEvent } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RecoveryDetails } from '../components/RecoveryDetails'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { badge, button, callout, card, fieldLabel } from '../ui/classes'
import { KIND_LABELS, RECORD_ID_PATTERN, RECORD_ID_LENGTH, STATE_LABELS } from '../drafts/api'
import { RECOVERY_REASON_MAX_LENGTH, type RecoveryInspection, type RecoveryResult } from './api'

type Props = { api: ApiClient; roles: string[]; onRelogin: () => void }

type Loaded =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'inspected'; recordId: string; inspection: RecoveryInspection }
  | { kind: 'error'; error: ApiError }

/** Admin の回復操作。結果確認中のまま止まった操作を、Registry の再取得で確定する */
export function RecoveryView({ api, roles, onRelogin }: Props) {
  const [recordId, setRecordId] = useState('')
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'idle' })
  const [result, setResult] = useState<RecoveryResult | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  if (!roles.includes('Admin')) return null

  async function inspect(event: FormEvent) {
    event.preventDefault()
    setLoaded({ kind: 'loading' })
    setResult(null)
    setError(null)
    setConfirmed(false)
    try {
      const inspection = await api.inspectRecovery(recordId)
      setLoaded({ kind: 'inspected', recordId, inspection })
    } catch (failure) {
      setLoaded({ kind: 'error', error: toApiError(failure, '結果確認中の操作を取得できませんでした。') })
    }
  }

  async function recover(target: string, release: boolean) {
    setBusy(true)
    setError(null)
    try {
      setResult(await api.recoverOperation(target, { reason, release }))
      setConfirmed(false)
    } catch (failure) {
      setResult(null)
      setError(toApiError(failure, '回復できませんでした。'))
    } finally {
      setBusy(false)
    }
  }

  const reasonValid = reason.trim().length > 0 && reason.length <= RECOVERY_REASON_MAX_LENGTH
  const target = loaded.kind === 'inspected' ? loaded.inspection.operation : null
  const unsettled = result !== null && result.operationState === 'VERIFYING'
  // 確定した後は、古い「結果確認中」の操作を並べて表示しない
  const showTarget = target !== null && (result === null || unsettled)

  return (
    <section aria-labelledby="recovery-heading" className="space-y-6">
      <PageHeader
        headingId="recovery-heading"
        title="回復操作"
        description={
          <p>結果確認中のまま止まった操作を、Registry を再取得して確定します。確定できない場合だけ、強制解除できます。</p>
        }
      />
      <form onSubmit={(event) => void inspect(event)} className={`${card} p-5 space-y-3 max-w-2xl`}>
        <h2 className="text-sm font-semibold text-slate-900">結果確認中の操作を照会</h2>
        <div className="flex flex-col sm:flex-row sm:items-end gap-2.5">
          <label className={`${fieldLabel} flex-1`}>
            Registry レコード ID
            <input
              name="recoveryRecordId"
              className="font-mono"
              value={recordId}
              pattern={RECORD_ID_PATTERN}
              maxLength={RECORD_ID_LENGTH}
              placeholder={`${RECORD_ID_LENGTH} 桁のレコード ID`}
              onChange={(event) => setRecordId(event.target.value)}
            />
          </label>
          <button
            type="submit"
            className={`${button.primary} shrink-0`}
            disabled={loaded.kind === 'loading' || recordId.length !== RECORD_ID_LENGTH}
          >
            結果確認中の操作を確認
          </button>
        </div>
      </form>
      {loaded.kind === 'loading' && <LoadingState>確認しています…</LoadingState>}
      {loaded.kind === 'error' && <RequestErrorAlert error={loaded.error} onRelogin={onRelogin} />}
      {loaded.kind === 'inspected' && target === null && result === null && (
        <EmptyState>結果確認中の操作はありません。</EmptyState>
      )}
      {loaded.kind === 'inspected' && target !== null && showTarget && (
        <section className={`${card} p-5 space-y-4`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className={badge.warning}>結果確認中</span>
            <span className="text-xs font-mono text-slate-500">recordId: {loaded.recordId}</span>
          </div>
          <p className="text-sm font-semibold text-slate-900">
            {KIND_LABELS[target.kind]}・結果確認中
          </p>
          <p className="text-xs text-slate-600 break-all">
            主体 {target.principal.subject}（{target.principal.issuer}）・{target.startedAt}
          </p>
          {!target.recoverable ? (
            <p className={callout.warning}>
              {target.recoverableAt} を過ぎるまで回復できません（実行中の可能性があるため）。時間をおいて再度確認してください。
            </p>
          ) : (
            <>
              <label className={fieldLabel}>
                回復の理由（必須。操作記録に残します）
                <textarea
                  name="recoveryReason"
                  className="font-sans text-sm"
                  value={reason}
                  maxLength={RECOVERY_REASON_MAX_LENGTH}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className={button.primary}
                  disabled={busy || !reasonValid}
                  onClick={() => void recover(loaded.recordId, false)}
                >
                  Registry を再取得して照合
                </button>
                <span className="text-xs text-slate-500">
                  ※ まず再取得で照合します。照合後も確定できないときだけ、強制解除を表示します。
                </span>
              </div>
              {unsettled && (
                <fieldset className="rounded-xl border border-rose-200 bg-rose-50/50 p-4 space-y-3">
                  <legend className="px-1 text-sm font-semibold text-rose-950">強制解除</legend>
                  <p className="text-xs text-rose-900">
                    Registry の状態から結果を確定できません。強制解除すると操作は失敗として確定し、書き込みの停止を解除します。
                  </p>
                  <label className="flex items-start gap-2 text-xs text-rose-900">
                    <input
                      type="checkbox"
                      name="recoveryConfirm"
                      className="mt-0.5"
                      checked={confirmed}
                      onChange={(event) => setConfirmed(event.target.checked)}
                    />
                    Registry の状態を確認し、操作が反映されていないことを確かめた
                  </label>
                  <button
                    type="button"
                    className={button.danger}
                    disabled={busy || !reasonValid || !confirmed}
                    onClick={() => void recover(loaded.recordId, true)}
                  >
                    強制解除する
                  </button>
                </fieldset>
              )}
            </>
          )}
        </section>
      )}
      {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
      {result && (
        <div role="status" className={`${unsettled ? callout.warning : callout.success} space-y-1`}>
          <p className="font-semibold">
            結果 {STATE_LABELS[result.operationState]}
            {unsettled ? '（結果確認中のままです。書き込みの停止は続いています）' : ''}
          </p>
          <p>{result.message}</p>
          {result.recovery && <RecoveryDetails recovery={result.recovery} />}
        </div>
      )}
    </section>
  )
}
