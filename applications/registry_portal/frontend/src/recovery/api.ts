// Admin の回復操作（Seam 2: SPA の fetch 境界）。結果確認中のまま止まった操作を、
// Registry の再取得で確定し、確定できなければ強制解除する。バックエンドの recovery.py と契約を揃える。
import { hasStrings, isObject } from '../api/guards'
import type { ApiRequest } from '../api/request'
import {
  OPERATION_KINDS,
  OPERATION_STATES,
  type OperationKind,
  type OperationState,
} from '../drafts/api'

/** 回復の理由の上限。バックエンドの REASON_MAX_LENGTH（recovery.py）と揃える */
export const RECOVERY_REASON_MAX_LENGTH = 255

/** 回復の方法。RECONCILED は Registry の再取得で確定、RELEASED は確定できないまま強制解除 */
export const RECOVERY_MODES = ['RECONCILED', 'RELEASED'] as const
export type RecoveryMode = (typeof RECOVERY_MODES)[number]

export type RecoveryPrincipal = { issuer: string; subject: string }

/** 回復した主体・時刻・理由。操作記録にも同じ値が残る */
export type RecoveryRecord = {
  mode: RecoveryMode
  by: RecoveryPrincipal
  at: string
  reason: string
}

/** レコードのロックを持つ操作の概要。操作 ID や中身（理由・ハッシュ）は返らない（ADR-0010） */
export type RecoveryTarget = {
  kind: OperationKind
  operationState: Extract<OperationState, 'VERIFYING'>
  principal: RecoveryPrincipal
  startedAt: string
  /** 開始から一定時間が過ぎ、実行中の可能性が低い。false の間は回復できない */
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

function isPrincipal(value: unknown): value is RecoveryPrincipal {
  return isObject(value) && hasStrings(value, ['issuer', 'subject'])
}
function isRecord(value: unknown): value is RecoveryRecord {
  return (
    isObject(value) &&
    RECOVERY_MODES.some((mode) => mode === value.mode) &&
    isPrincipal(value.by) &&
    hasStrings(value, ['at', 'reason'])
  )
}
function isTarget(value: unknown): value is RecoveryTarget {
  return (
    isObject(value) &&
    hasStrings(value, ['startedAt']) &&
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
    (value.recovery === null || isRecord(value.recovery))
  )
}

export function createRecoveryApi(request: ApiRequest, invalidResponse: () => Error): RecoveryApi {
  async function checked<T>(response: Promise<unknown>, guard: (value: unknown) => value is T): Promise<T> {
    const value = await response
    if (!guard(value)) throw invalidResponse()
    return value
  }
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
