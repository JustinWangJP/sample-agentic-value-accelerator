// 公開管理（Seam 2: SPA の fetch 境界）。Curator の公開停止・廃止・廃止依頼の見送りと、その判断に使う照会。
// バックエンドの lifecycle.py・curation.py・deprecation.py と契約を揃える。クライアントから AWS の status は送らない。
import { hasStrings, isObject } from '../api/guards'
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import { createResponseChecker, type ApiRequest } from '../api/request'
import {
  isDeprecationRequest,
  isRecordStatus,
  type DeprecationRequest,
  type RecordStatus,
} from '../drafts/api'
import { isDecisionOperation, type DecisionOperation } from '../reviews/api'

/**
 * 公開停止・廃止。API の path（/api/records/{recordId}/suspend・/deprecate）と、照会の actions のキー。
 * バックエンドの LIFECYCLE_ACTIONS（domain/curation.py）と揃える
 */
export const LIFECYCLE_ACTIONS = ['suspend', 'deprecate'] as const
export type LifecycleAction = (typeof LIFECYCLE_ACTIONS)[number]

/** いま実行できるか。できなければ、その理由（利用者に見せる文言）を持つ */
export type ActionAvailability = { allowed: boolean; message: string | null }

/** 公開管理の照会の結果。公開停止・廃止・見送りを判断する前に確認する状態 */
export type LifecycleView = {
  recordId: string
  name: string
  displayName: string
  recordType: CatalogRecordType
  recordVersion: string
  status: RecordStatus
  managed: boolean
  ownerTeam: string | null
  /** 発見できる版。公開停止中と廃止済みでは null */
  publishedVersion: string | null
  /** 公開停止中（再承認まで） */
  suspended: boolean
  /** レコードのロックを持つ操作（結果確認中など） */
  operationId: string | null
  /** 未解決の廃止依頼 */
  deprecationRequest: DeprecationRequest | null
  actions: Record<LifecycleAction, ActionAvailability>
}

/** 公開停止の入力。バックエンドの LifecycleInput（SUSPEND）と揃える */
export type SuspendInput = { reason: string }
/** 廃止の入力。元に戻せないため確認（confirm）が必須（バックエンドの LifecycleInput（DEPRECATE）） */
export type DeprecateInput = { reason: string; confirm: true }
/** 見送りの入力。画面で確認した依頼（requestId）と一致するときだけ見送る */
export type DeclineInput = { requestId: string; reason: string }

export type LifecycleApi = {
  getLifecycle: (recordId: string) => Promise<LifecycleView>
  suspendRecord: (
    recordId: string,
    input: SuspendInput,
    idempotencyKey: string,
  ) => Promise<DecisionOperation>
  deprecateRecord: (
    recordId: string,
    input: DeprecateInput,
    idempotencyKey: string,
  ) => Promise<DecisionOperation>
  declineDeprecationRequest: (recordId: string, input: DeclineInput) => Promise<DeprecationRequest>
}

function isAvailability(value: unknown): value is ActionAvailability {
  return (
    isObject(value) &&
    typeof value.allowed === 'boolean' &&
    (value.message === null || typeof value.message === 'string')
  )
}

function isLifecycleView(value: unknown): value is LifecycleView {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) &&
    isRecordStatus(value.status) &&
    typeof value.managed === 'boolean' &&
    (value.ownerTeam === null || typeof value.ownerTeam === 'string') &&
    (value.publishedVersion === null || typeof value.publishedVersion === 'string') &&
    typeof value.suspended === 'boolean' &&
    (value.operationId === null || typeof value.operationId === 'string') &&
    (value.deprecationRequest === null || isDeprecationRequest(value.deprecationRequest)) &&
    isObject(value.actions) &&
    LIFECYCLE_ACTIONS.every((action) => isAvailability((value.actions as Record<string, unknown>)[action]))
  )
}

/** 滞留している依頼を見分けるための、依頼からの経過日数（切り捨て）。日時が読めなければ null */
export function elapsedDays(since: string, now: Date = new Date()): number | null {
  const started = Date.parse(since)
  if (Number.isNaN(started)) return null
  return Math.max(0, Math.floor((now.getTime() - started) / 86_400_000))
}

export function createLifecycleApi(request: ApiRequest, invalidResponse: () => Error): LifecycleApi {
  const checked = createResponseChecker(invalidResponse)
  const recordPath = (id: string) => `/api/records/${encodeURIComponent(id)}`
  return {
    getLifecycle: (id) => checked(request('GET', `${recordPath(id)}/lifecycle`), isLifecycleView),
    suspendRecord: (id, input, key) =>
      checked(
        request('POST', `${recordPath(id)}/suspend`, input, { 'idempotency-key': key }),
        isDecisionOperation,
      ),
    deprecateRecord: (id, input, key) =>
      checked(
        request('POST', `${recordPath(id)}/deprecate`, input, { 'idempotency-key': key }),
        isDecisionOperation,
      ),
    declineDeprecationRequest: (id, input) =>
      checked(
        request('POST', `${recordPath(id)}/deprecation-requests/decline`, input),
        isDeprecationRequest,
      ),
  }
}
