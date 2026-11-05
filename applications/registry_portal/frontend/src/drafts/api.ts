import { DATA_CLASSIFICATIONS, type DataClassification } from '../api/client'

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

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function strings(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof value[key] === 'string')
}
function isDraft(value: unknown): value is AgentDraft {
  return (
    object(value) &&
    DRAFT_TEXT_FIELDS.every(([key]) => typeof value[key] === 'string') &&
    typeof value.ownerTeam === 'string' &&
    value.environment === 'dev' &&
    DATA_CLASSIFICATIONS.some((classification) => classification === value.dataClassification)
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
    object(value) &&
    typeof value.recordId === 'string' &&
    AWS_RECORD_STATUSES.some((status) => status === value.status) &&
    typeof value.hasDraft === 'boolean' &&
    (value.publishedVersion === null || typeof value.publishedVersion === 'string')
  )
}
function isDetail(value: unknown): value is DraftDetail {
  return (
    object(value) &&
    isRecord(value) &&
    isDraft(value.draft) &&
    typeof value.contentHash === 'string' &&
    /^[a-f0-9]{64}$/.test(value.contentHash)
  )
}
function isSummary(value: unknown): value is DraftSummary {
  return (
    object(value) &&
    isRecord(value) &&
    strings(value, ['name', 'displayName', 'recordVersion', 'ownerTeam']) &&
    (value.operationId === null || typeof value.operationId === 'string')
  )
}
function isPage(value: unknown): value is DraftPage {
  return (
    object(value) &&
    Array.isArray(value.items) &&
    value.items.every(isSummary) &&
    (value.nextToken === null || typeof value.nextToken === 'string')
  )
}
function isOperation(value: unknown): value is DraftOperation {
  return (
    object(value) &&
    typeof value.operationId === 'string' &&
    ((value.operationState === 'SUCCEEDED' && isDetail(value)) ||
      ((value.operationState === 'VERIFYING' || value.operationState === 'FAILED') &&
        typeof value.message === 'string'))
  )
}

type Request = (
  method: 'GET' | 'POST' | 'PATCH',
  path: string,
  payload?: unknown,
  headers?: Record<string, string>,
) => Promise<unknown>
export function createDraftApi(request: Request, invalidResponse: () => Error): DraftApi {
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
