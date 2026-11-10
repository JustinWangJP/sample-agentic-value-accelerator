import { useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RecoveredOperationNotice } from '../components/RecoveryDetails'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import type { DraftOperation } from './api'

export function OperationCheck({
  api,
  operationId,
  onDone,
  onRelogin,
}: {
  api: ApiClient
  operationId: string
  onDone: (operation: DraftOperation) => void
  onRelogin: () => void
}) {
  const [message, setMessage] = useState('結果確認中です。確定するまで書き込みを停止しています。')
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  // Admin の回復操作で確定した操作は、回復結果を見せてから結果の表示へ進む
  const [recovered, setRecovered] = useState<DraftOperation | null>(null)
  async function check() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.getDraftOperation(operationId)
      if (result.operationState === 'VERIFYING') setMessage(result.message)
      else if (result.recovery) setRecovered(result)
      else onDone(result)
    } catch (failure) {
      setError(toApiError(failure, '処理結果を取得できませんでした。'))
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
        処理結果を確認
      </button>
      {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
    </div>
  )
}

