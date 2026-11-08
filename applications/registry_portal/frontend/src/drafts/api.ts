import { isDataClassification } from '../api/dataClassification'
import { hasStrings, isObject } from '../api/guards'
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import type { ApiRequest } from '../api/request'
import {
  COMMON_TEXT_FIELDS,
  DESCRIPTOR_FIELD_SPECS,
  isAllowedKind,
  isDescriptorType,
  type RecordDraft,
} from './descriptors'

export type { RecordDraft }

/** 下書きを保存できる environment。バックエンドの ENVIRONMENTS（domain/drafts.py）と揃える */
export const DRAFT_ENVIRONMENTS = ['dev'] as const
export type DraftEnvironment = (typeof DRAFT_ENVIRONMENTS)[number]
export const DEFAULT_DRAFT_ENVIRONMENT: DraftEnvironment = DRAFT_ENVIRONMENTS[0]
export function isDraftEnvironment(value: unknown): value is DraftEnvironment {
  return DRAFT_ENVIRONMENTS.some((environment) => environment === value)
}


/** AWS の Registry レコードの状態。バックエンドの RecordStatus（domain/drafts.py）と揃える */
export const AWS_RECORD_STATUSES = [
  'DRAFT',
  'PENDING_APPROVAL',
  'APPROVED',
  'REJECTED',
  'DEPRECATED',
  'CREATING',
  'UPDATING',
  'CREATE_FAILED',
  'UPDATE_FAILED',
] as const
export type RecordStatus = (typeof AWS_RECORD_STATUSES)[number]
export function isRecordStatus(value: unknown): value is RecordStatus {
  return AWS_RECORD_STATUSES.some((status) => status === value)
}
/** 審査待ち。画面では「審査待ち」と表示する（GLOSSARY「申請」）。バックエンドの PENDING_APPROVAL_STATUS と揃える */
export const PENDING_APPROVAL_STATUS: RecordStatus = 'PENDING_APPROVAL'
/** 公開承認済み。バックエンドの PUBLISHED_STATUS と揃える */
export const PUBLISHED_STATUS: RecordStatus = 'APPROVED'
/** 編集を受け付けない状態。バックエンドの NOT_EDITABLE_STATUSES と揃える */
const NOT_EDITABLE_STATUSES: readonly RecordStatus[] = ['CREATING', 'UPDATING', 'DEPRECATED']
export function isEditableStatus(status: RecordStatus): boolean {
  return !NOT_EDITABLE_STATUSES.includes(status)
}
export type DraftDetail = {
  recordId: string
  draft: RecordDraft
  contentHash: string
  status: RecordStatus
  hasDraft: boolean
  publishedVersion: string | null
}
export type DraftSummary = Omit<DraftDetail, 'draft' | 'contentHash'> & {
  recordType: CatalogRecordType
  name: string
  displayName: string
  recordVersion: string
  ownerTeam: string
  operationId: string | null
}
export type DraftPage = { items: DraftSummary[]; nextToken: string | null }
/** Registry レコード ID の長さと形式。バックエンドの RECORD_ID_LENGTH・RECORD_ID_PATTERN（domain/drafts.py）と揃える */
export const RECORD_ID_LENGTH = 12
export const RECORD_ID_PATTERN = `[A-Za-z0-9]{${RECORD_ID_LENGTH}}`
/** 申請入力の上限。バックエンドの REASON_MAX_LENGTH・RELATED_REFERENCES_MAX（domain/submissions.py）と揃える */
export const SUBMISSION_REASON_MAX_LENGTH = 4096
export const RELATED_REFERENCES_MAX = 20

export type DraftSubmission = {
  expectedContentHash: string
  reason: string
  relatedReferences: { recordId: string; recordVersion: string }[]
}

/**
 * API が返す書き込み操作の状態。バックエンドの OperationState（domain/drafts.py）のうち、
 * 内部でだけ使う IN_PROGRESS を除いた値と揃える
 */
export const OPERATION_STATES = ['SUCCEEDED', 'VERIFYING', 'FAILED'] as const
export type OperationState = (typeof OPERATION_STATES)[number]
export type UnsettledOperationState = Exclude<OperationState, 'SUCCEEDED'>
export function isUnsettledOperationState(value: unknown): value is UnsettledOperationState {
  return OPERATION_STATES.some((state) => state !== 'SUCCEEDED' && state === value)
}
export type DraftOperation =
  | (DraftDetail & { operationId: string; operationState: Extract<OperationState, 'SUCCEEDED'> })
  | { operationId: string; operationState: UnsettledOperationState; message: string }

/**
 * AWS への書き込みが行われていないと確定できるエラーのコード（バックエンドの drafts.py）。
 * 5xx でも「結果が不明」とは扱わない
 */
export const DRAFTS_NOT_CONFIGURED = 'DRAFTS_NOT_CONFIGURED'
/** AWS が混雑で処理しなかった（429）。その Idempotency-Key の操作は失敗として確定している */
export const REGISTRY_THROTTLED = 'REGISTRY_THROTTLED'
/** 自動承認の設定が想定外のため申請を止めた（503）。AWS へ書き込んでおらず、結果は確定している */
export const APPROVAL_CONFIGURATION_CHANGED = 'APPROVAL_CONFIGURATION_CHANGED'

/** 書き込み（作成・保存・申請）の失敗のうち、AWS に届いたかどうかが分からないものか */
export function isUncertainWriteError(error: { status: number; code: string }): boolean {
  return (
    (error.status === 0 || error.status >= 500) &&
    error.code !== DRAFTS_NOT_CONFIGURED &&
    error.code !== APPROVAL_CONFIGURATION_CHANGED
  )
}

/**
 * 作成・申請の失敗のうち、次は新しい Idempotency-Key が必要なものか。
 * 結果が不明な間は同じキーで照合を続け、それ以外（確定した失敗）はキーを作り直す
 */
export function settlesIdempotencyKey(error: { status: number; code: string }): boolean {
  return !isUncertainWriteError(error)
}

export type DraftApi = {
  listDrafts: (nextToken?: string) => Promise<DraftPage>
  getDraft: (recordId: string) => Promise<DraftDetail>
  createDraft: (draft: RecordDraft, idempotencyKey: string) => Promise<DraftOperation>
  updateDraft: (
    recordId: string,
    draft: RecordDraft,
    expectedContentHash: string,
  ) => Promise<DraftOperation>
  getDraftOperation: (operationId: string) => Promise<DraftOperation>
  submitDraft: (
    recordId: string,
    submission: DraftSubmission,
    idempotencyKey: string,
  ) => Promise<DraftOperation>
}

function isDraft(value: unknown): value is RecordDraft {
  return (
    isObject(value) &&
    isDescriptorType(value.descriptorType) &&
    isAllowedKind(value.recordType, value.descriptorType) &&
    COMMON_TEXT_FIELDS.every(({ key }) => typeof value[key] === 'string') &&
    DESCRIPTOR_FIELD_SPECS[value.descriptorType].every(
      (spec) => typeof value[spec.key] === 'string',
    ) &&
    typeof value.ownerTeam === 'string' &&
    isDraftEnvironment(value.environment) &&
    isDataClassification(value.dataClassification)
  )
}
function isRecord(
  value: unknown,
): value is Record<string, unknown> & {
  recordId: string
  status: RecordStatus
  hasDraft: boolean
  publishedVersion: string | null
} {
  return (
    isObject(value) &&
    typeof value.recordId === 'string' &&
    isRecordStatus(value.status) &&
    typeof value.hasDraft === 'boolean' &&
    (value.publishedVersion === null || typeof value.publishedVersion === 'string')
  )
}
function isDetail(value: unknown): value is DraftDetail {
  return (
    isObject(value) &&
    isRecord(value) &&
    isDraft(value.draft) &&
    typeof value.contentHash === 'string' &&
    /^[a-f0-9]{64}$/.test(value.contentHash)
  )
}
function isSummary(value: unknown): value is DraftSummary {
  return (
    isObject(value) &&
    isRecord(value) &&
    hasStrings(value, ['name', 'displayName', 'recordVersion', 'ownerTeam']) &&
    isCatalogRecordType(value.recordType) &&
    (value.operationId === null || typeof value.operationId === 'string')
  )
}
function isPage(value: unknown): value is DraftPage {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isSummary) &&
    (value.nextToken === null || typeof value.nextToken === 'string')
  )
}
function isOperation(value: unknown): value is DraftOperation {
  return (
    isObject(value) &&
    typeof value.operationId === 'string' &&
    ((value.operationState === 'SUCCEEDED' && isDetail(value)) ||
      (isUnsettledOperationState(value.operationState) && typeof value.message === 'string'))
  )
}

export function createDraftApi(request: ApiRequest, invalidResponse: () => Error): DraftApi {
  async function checked<T>(
    response: Promise<unknown>,
    guard: (value: unknown) => value is T,
  ): Promise<T> {
    const value = await response
    if (!guard(value)) throw invalidResponse()
    return value
  }
  return {
    listDrafts: (token) =>
      checked(
        request(
          'GET',
          `/api/records${token === undefined ? '' : `?${new URLSearchParams({ nextToken: token })}`}`,
        ),
        isPage,
      ),
    getDraft: (id) => checked(request('GET', `/api/records/${encodeURIComponent(id)}`), isDetail),
    createDraft: (draft, key) =>
      checked(request('POST', '/api/records', draft, { 'idempotency-key': key }), isOperation),
    updateDraft: (id, draft, hash) =>
      checked(
        request('PATCH', `/api/records/${encodeURIComponent(id)}`, {
          draft,
          expectedContentHash: hash,
        }),
        isOperation,
      ),
    getDraftOperation: (id) =>
      checked(request('GET', `/api/operations/${encodeURIComponent(id)}`), isOperation),
    submitDraft: (id, submission, key) =>
      checked(
        request('POST', `/api/records/${encodeURIComponent(id)}/submit`, submission, {
          'idempotency-key': key,
        }),
        isOperation,
      ),
  }
}
