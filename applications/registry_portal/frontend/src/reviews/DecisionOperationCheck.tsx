import { useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RecoveredOperationNotice } from '../components/RecoveryDetails'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import type { DecisionOperation } from './api'

/**
 * 結果確認中の決裁（公開承認・却下・公開停止・廃止）の結果を、操作照会で確かめる。
 * 確定するまでレコードへの書き込みは止まっている（ADR-0010）
 */
export function DecisionOperationCheck({
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
