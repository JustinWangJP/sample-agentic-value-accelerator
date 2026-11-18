import { useState, type FormEvent } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RecoveryDetails } from '../components/RecoveryDetails'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
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
    <section aria-labelledby="recovery-heading">
      <h2 id="recovery-heading">回復操作</h2>
      <p className="note">
        結果確認中のまま止まった操作を、Registry を再取得して確定します。確定できない場合だけ、強制解除できます。
      </p>
      <form onSubmit={(event) => void inspect(event)}>
        <label>
          Registry レコード ID
          <input
            name="recoveryRecordId"
            value={recordId}
            pattern={RECORD_ID_PATTERN}
            maxLength={RECORD_ID_LENGTH}
            onChange={(event) => setRecordId(event.target.value)}
          />
        </label>
        <button type="submit" disabled={loaded.kind === 'loading' || recordId.length !== RECORD_ID_LENGTH}>
          結果確認中の操作を確認
        </button>
      </form>
      {loaded.kind === 'loading' && <p role="status">確認しています…</p>}
      {loaded.kind === 'error' && <RequestErrorAlert error={loaded.error} onRelogin={onRelogin} />}
      {loaded.kind === 'inspected' && target === null && result === null && (
        <p>結果確認中の操作はありません。</p>
      )}
      {loaded.kind === 'inspected' && target !== null && showTarget && (
        <div>
          <p>
            {KIND_LABELS[target.kind]}・結果確認中
          </p>
          <p>
            主体 {target.principal.subject}（{target.principal.issuer}）・{target.startedAt}
          </p>
          {!target.recoverable ? (
            <p>
              {target.recoverableAt} を過ぎるまで回復できません（実行中の可能性があるため）。時間をおいて再度確認してください。
            </p>
          ) : (
            <>
              <label>
                回復の理由（必須。操作記録に残します）
                <textarea
                  name="recoveryReason"
                  value={reason}
                  maxLength={RECOVERY_REASON_MAX_LENGTH}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={busy || !reasonValid}
                onClick={() => void recover(loaded.recordId, false)}
              >
                Registry を再取得して照合
              </button>
              {unsettled && (
                <fieldset>
                  <legend>強制解除</legend>
                  <p>
                    Registry の状態から結果を確定できません。強制解除すると操作は失敗として確定し、書き込みの停止を解除します。
                  </p>
                  <label>
                    <input
                      type="checkbox"
                      name="recoveryConfirm"
                      checked={confirmed}
                      onChange={(event) => setConfirmed(event.target.checked)}
                    />
                    Registry の状態を確認し、操作が反映されていないことを確かめた
                  </label>
                  <button
                    type="button"
                    disabled={busy || !reasonValid || !confirmed}
                    onClick={() => void recover(loaded.recordId, true)}
                  >
                    強制解除する
                  </button>
                </fieldset>
              )}
            </>
          )}
        </div>
      )}
      {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
      {result && (
        <div role="status">
          <p>
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
