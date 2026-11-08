import { isDataClassification } from '../api/dataClassification'
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
type UnsettledOperationState = Exclude<OperationState, 'SUCCEEDED'>
function isUnsettledOperationState(value: unknown): value is UnsettledOperationState {
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

/** 保存の失敗のうち、AWS に届いたかどうかが分からないものか */
export function isUncertainSaveError(error: { status: number; code: string }): boolean {
  return (error.status === 0 || error.status >= 500) && error.code !== DRAFTS_NOT_CONFIGURED
}

/** 作成の失敗のうち、その Idempotency-Key の操作が失敗として確定し、次は新しいキーが必要なものか */
export function settlesCreateKey(error: { status: number; code: string }): boolean {
  return error.status === 400 || error.code === REGISTRY_THROTTLED
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
  submitDraft: (recordId: string, submission: DraftSubmission) => Promise<DraftOperation>
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function hasStringFields(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof value[key] === 'string')
}
function isDraft(value: unknown): value is RecordDraft {
  return (
    isPlainObject(value) &&
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
    isPlainObject(value) &&
    typeof value.recordId === 'string' &&
    AWS_RECORD_STATUSES.some((status) => status === value.status) &&
    typeof value.hasDraft === 'boolean' &&
    (value.publishedVersion === null || typeof value.publishedVersion === 'string')
  )
}
function isDetail(value: unknown): value is DraftDetail {
  return (
    isPlainObject(value) &&
    isRecord(value) &&
    isDraft(value.draft) &&
    typeof value.contentHash === 'string' &&
    /^[a-f0-9]{64}$/.test(value.contentHash)
  )
}
function isSummary(value: unknown): value is DraftSummary {
  return (
    isPlainObject(value) &&
    isRecord(value) &&
    hasStringFields(value, ['name', 'displayName', 'recordVersion', 'ownerTeam']) &&
    isCatalogRecordType(value.recordType) &&
    (value.operationId === null || typeof value.operationId === 'string')
  )
}
function isPage(value: unknown): value is DraftPage {
  return (
    isPlainObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isSummary) &&
    (value.nextToken === null || typeof value.nextToken === 'string')
  )
}
function isOperation(value: unknown): value is DraftOperation {
  return (
    isPlainObject(value) &&
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
    submitDraft: (id, submission) =>
      checked(request('POST', `/api/records/${encodeURIComponent(id)}/submit`, submission), isOperation),
  }
}
