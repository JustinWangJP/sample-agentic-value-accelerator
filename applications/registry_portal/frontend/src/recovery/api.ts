// Admin の回復操作（Seam 2: SPA の fetch 境界）。結果確認中のまま止まった操作を、
// Registry の再取得で確定し、確定できなければ強制解除する。バックエンドの recovery.py と契約を揃える。
import { hasStrings, isObject, isPrincipal, type Principal } from '../api/guards'
import { createResponseChecker, type ApiRequest } from '../api/request'
import {
  OPERATION_KINDS,
  OPERATION_STATES,
  isRecoveryRecord,
  type OperationKind,
  type OperationState,
  type RecoveryRecord,
} from '../drafts/api'

/** 回復の理由の上限。バックエンドの RECOVERY_REASON_MAX_LENGTH（recovery.py）と揃える */
export const RECOVERY_REASON_MAX_LENGTH = 255

/** レコードのロックを持つ操作の概要。操作 ID や中身（理由・ハッシュ）は返らない（ADR-0010） */
export type RecoveryTarget = {
  kind: OperationKind
  operationState: Extract<OperationState, 'VERIFYING'>
  principal: Principal
  startedAt: string
  /** 実行中の可能性がなくなり、回復できるようになる時刻。待ち時間はバックエンドだけが持つ */
  recoverableAt: string
  /** recoverableAt を過ぎた。false の間は回復できない */
  recoverable: boolean
}

export type RecoveryInspection = { recordId: string; operation: RecoveryTarget | null }

export type RecoveryResult = {
  recordId: string
  operationState: OperationState
  message: string
  /** 確定していない（結果確認中のまま）ときは null */
  recovery: RecoveryRecord | null
}

export type RecoveryApi = {
  inspectRecovery: (recordId: string) => Promise<RecoveryInspection>
  recoverOperation: (
    recordId: string,
    input: { reason: string; release: boolean },
  ) => Promise<RecoveryResult>
}

function isTarget(value: unknown): value is RecoveryTarget {
  return (
    isObject(value) &&
    hasStrings(value, ['startedAt', 'recoverableAt']) &&
    OPERATION_KINDS.some((kind) => kind === value.kind) &&
    value.operationState === 'VERIFYING' &&
    isPrincipal(value.principal) &&
    typeof value.recoverable === 'boolean'
  )
}
function isInspection(value: unknown): value is RecoveryInspection {
  return (
    isObject(value) &&
    typeof value.recordId === 'string' &&
    (value.operation === null || isTarget(value.operation))
  )
}
function isResult(value: unknown): value is RecoveryResult {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'message']) &&
    OPERATION_STATES.some((state) => state === value.operationState) &&
    (value.recovery === null || isRecoveryRecord(value.recovery))
  )
}

export function createRecoveryApi(request: ApiRequest, invalidResponse: () => Error): RecoveryApi {
  const checked = createResponseChecker(invalidResponse)
  const path = (recordId: string) => `/api/records/${encodeURIComponent(recordId)}/recovery`
  return {
    inspectRecovery: (recordId) => checked(request('GET', path(recordId)), isInspection),
    recoverOperation: (recordId, input) =>
      checked(
        request('POST', path(recordId), input.release ? input : { reason: input.reason }),
        isResult,
      ),
  }
}
