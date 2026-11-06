import { isDataClassification, type DataClassification } from '../api/dataClassification'
import type { ApiRequest } from '../api/request'

export type AgentDraft = {
  name: string
  displayName: string
  description: string
  recordVersion: string
  ownerTeam: string
  environment: 'dev'
  dataClassification: DataClassification
  purpose: string
  connectionReference: string
  authentication: string
  contact: string
  termsOfUse: string
}

export const DRAFT_TEXT_FIELDS = [
  ['name', '名前', 255],
  ['displayName', '表示名', 255],
  ['description', '説明', 4096],
  ['recordVersion', '版（semver）', 255],
  ['purpose', '目的', 4096],
  ['connectionReference', '接続先の参照', 2048],
  ['authentication', '認証方式', 1024],
  ['contact', '連絡先', 1024],
  ['termsOfUse', '利用条件', 4096],
] as const satisfies readonly (readonly [keyof AgentDraft, string, number])[]

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
  draft: AgentDraft
  contentHash: string
  status: RecordStatus
  hasDraft: boolean
  publishedVersion: string | null
}
export type DraftSummary = Omit<DraftDetail, 'draft' | 'contentHash'> & {
  name: string
  displayName: string
  recordVersion: string
  ownerTeam: string
  operationId: string | null
}
export type DraftPage = { items: DraftSummary[]; nextToken: string | null }
export type DraftOperation =
  | (DraftDetail & { operationId: string; operationState: 'SUCCEEDED' })
  | { operationId: string; operationState: 'VERIFYING' | 'FAILED'; message: string }

export type DraftApi = {
  listDrafts: (nextToken?: string) => Promise<DraftPage>
  getDraft: (recordId: string) => Promise<DraftDetail>
  createDraft: (draft: AgentDraft, idempotencyKey: string) => Promise<DraftOperation>
  updateDraft: (
    recordId: string,
    draft: AgentDraft,
    expectedContentHash: string,
  ) => Promise<DraftOperation>
  getDraftOperation: (operationId: string) => Promise<DraftOperation>
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function hasStringFields(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof value[key] === 'string')
}
function isDraft(value: unknown): value is AgentDraft {
  return (
    isPlainObject(value) &&
    DRAFT_TEXT_FIELDS.every(([key]) => typeof value[key] === 'string') &&
    typeof value.ownerTeam === 'string' &&
    value.environment === 'dev' &&
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
      ((value.operationState === 'VERIFYING' || value.operationState === 'FAILED') &&
        typeof value.message === 'string'))
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
  }
}
