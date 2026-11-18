import { useState } from 'react'
import { toApiError, type ApiError } from '../api/client'
import type { OperationState, RecoveryRecord, UnsettledOperationState } from '../drafts/api'
import { RecoveredOperationNotice } from './RecoveryDetails'
import { RequestErrorAlert } from './RequestErrorAlert'

/** 操作照会が返す操作のうち、結果確認で見る項目。下書きの操作と決裁 handler の操作で共通 */
type CheckedOperation =
  | { operationState: Extract<OperationState, 'SUCCEEDED'>; recovery?: RecoveryRecord }
  | { operationState: UnsettledOperationState; message: string; recovery?: RecoveryRecord }

/**
 * 結果確認中の操作の結果を、操作照会で確かめる。確定するまでレコードへの書き込みは止まっている（ADR-0010）。
 * 下書きの保存・申請と、決裁 handler の操作（決裁である公開承認・却下と、公開停止・廃止）で同じ表示を使う。
 * 操作の種別によらない文言にする（公開停止・廃止は決裁ではないため。GLOSSARY.md「決裁」）
 */
export function OperationCheck<T extends CheckedOperation>({
  operationId,
  load,
  onDone,
  onRelogin,
}: {
  operationId: string
  /** 操作照会。下書きの操作と決裁 handler の操作で照会の API が異なる */
  load: (operationId: string) => Promise<T>
  onDone: (operation: T) => void
  onRelogin: () => void
}) {
  const [message, setMessage] = useState('結果確認中です。確定するまでこのレコードへの書き込みを停止しています。')
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  // Admin の回復操作で確定した操作は、回復結果を見せてから結果の表示へ進む
  const [recovered, setRecovered] = useState<T | null>(null)
  async function check() {
    setBusy(true)
    setError(null)
    try {
      const result = await load(operationId)
      const checked: CheckedOperation = result
      if (checked.operationState === 'VERIFYING') setMessage(checked.message)
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
