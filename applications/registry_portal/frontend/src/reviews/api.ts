// 審査と決裁（Seam 2: SPA の fetch 境界）。Curator が審査待ちの一覧と詳細を見て、公開承認・却下を行う。
// バックエンドの curation.py と契約を揃える。クライアントから AWS の status は送らない。
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import type { ApiRequest } from '../api/request'
import type { RecordStatus } from '../drafts/api'

/** 決裁理由の上限。AWS の statusReason（0〜255文字）に合わせる（バックエンドの REASON_MAX_LENGTH） */
export const REVIEW_REASON_MAX_LENGTH = 255

export type ReviewReference = { recordId: string; recordVersion: string }

export type ReviewPrincipal = { issuer: string; subject: string }

/** 審査待ちの一覧の項目。AWS の承認待ちを正本とし、申請の補助情報を結合したもの */
export type ReviewSummary = {
  recordId: string
  name: string
  displayName: string
  recordType: CatalogRecordType
  recordVersion: string
  status: RecordStatus
  managed: boolean
  ownerTeam: string | null
  submissionId: string | null
  snapshotHash: string | null
  reason: string | null
  submittedAt: string | null
  submittedBy: ReviewPrincipal | null
  invalidated: boolean
  operationId: string | null
}

export type ReviewPage = { items: ReviewSummary[]; nextToken: string | null }

/** 差分の比較対象（公開中の版のスナップショット）。ADR-0007 の公開版スナップショットから読む */
export type PublishedBase = {
  recordVersion: string
  contact: string | null
  termsOfUse: string | null
  connectionReference: string | null
  relatedReferences: ReviewReference[]
}

/** 申請のスナップショット。Curator が審査し、決裁時にハッシュで固定する対象 */
export type SubmissionSnapshot = {
  submissionId: string
  submittedBy: ReviewPrincipal
  lastEditedBy: ReviewPrincipal
  submittedAt: string
  reason: string
  record: Record<string, unknown>
  ownerTeam: string
  supplement: Record<string, unknown>
  relatedReferences: ReviewReference[]
  contact: string
  termsOfUse: string
  connectionReference: string
  contentHash: string
  snapshotHash: string
}

export type ReviewDetail = {
  recordId: string
  name: string
  displayName: string
  description: string
  recordType: CatalogRecordType
  recordVersion: string
  ownerTeam: string | null
  status: RecordStatus
  managed: boolean
  publishedVersion: string | null
  submissionId: string | null
  snapshotHash: string | null
  invalidated: boolean
  snapshot: SubmissionSnapshot | null
  published: PublishedBase | null
}

/** 決裁の結果。SUCCEEDED は決裁が AWS に反映され、公開版スナップショットも保存済み */
export type DecisionOperation =
  | (Omit<ReviewSummary, 'managed' | 'ownerTeam' | 'submissionId' | 'snapshotHash' | 'reason' | 'submittedAt' | 'submittedBy' | 'invalidated' | 'operationId'> & {
      operationId: string
      operationState: 'SUCCEEDED'
      ownerTeam: string
      publishedVersion: string | null
      submissionId: string
      snapshotHash: string
    })
  | { operationId: string; operationState: 'VERIFYING' | 'FAILED'; message: string }

export type DecisionInput = { snapshotHash: string; reason: string }

export type ReviewApi = {
  listReviews: (nextToken?: string) => Promise<ReviewPage>
  getReview: (recordId: string) => Promise<ReviewDetail>
  approveReview: (recordId: string, input: DecisionInput, idempotencyKey: string) => Promise<DecisionOperation>
  rejectReview: (recordId: string, input: DecisionInput, idempotencyKey: string) => Promise<DecisionOperation>
  getDecisionOperation: (operationId: string) => Promise<DecisionOperation>
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function hasStrings(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof value[key] === 'string')
}
function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}
function isPrincipal(value: unknown): value is ReviewPrincipal {
  return isObject(value) && hasStrings(value, ['issuer', 'subject'])
}
function isReference(value: unknown): value is ReviewReference {
  return isObject(value) && hasStrings(value, ['recordId', 'recordVersion'])
}
function isReferenceList(value: unknown): value is ReviewReference[] {
  return Array.isArray(value) && value.every(isReference)
}

function isSummary(value: unknown): value is ReviewSummary {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) &&
    typeof value.status === 'string' &&
    typeof value.managed === 'boolean' &&
    isStringOrNull(value.ownerTeam) &&
    isStringOrNull(value.submissionId) &&
    isStringOrNull(value.snapshotHash) &&
    isStringOrNull(value.reason) &&
    isStringOrNull(value.submittedAt) &&
    (value.submittedBy === null || isPrincipal(value.submittedBy)) &&
    typeof value.invalidated === 'boolean' &&
    (value.operationId === null || typeof value.operationId === 'string')
  )
}
function isPage(value: unknown): value is ReviewPage {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isSummary) &&
    (value.nextToken === null || typeof value.nextToken === 'string')
  )
}
function isPublished(value: unknown): value is PublishedBase {
  return (
    isObject(value) &&
    hasStrings(value, ['recordVersion']) &&
    isStringOrNull(value.contact) &&
    isStringOrNull(value.termsOfUse) &&
    isStringOrNull(value.connectionReference) &&
    isReferenceList(value.relatedReferences)
  )
}
function isSnapshot(value: unknown): value is SubmissionSnapshot {
  return (
    isObject(value) &&
    hasStrings(value, [
      'submissionId',
      'submittedAt',
      'reason',
      'ownerTeam',
      'contact',
      'termsOfUse',
      'connectionReference',
      'contentHash',
      'snapshotHash',
    ]) &&
    isPrincipal(value.submittedBy) &&
    isPrincipal(value.lastEditedBy) &&
    isObject(value.record) &&
    isObject(value.supplement) &&
    isReferenceList(value.relatedReferences)
  )
}
function isDetail(value: unknown): value is ReviewDetail {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'description', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) &&
    typeof value.status === 'string' &&
    typeof value.managed === 'boolean' &&
    isStringOrNull(value.ownerTeam) &&
    isStringOrNull(value.publishedVersion) &&
    isStringOrNull(value.submissionId) &&
    isStringOrNull(value.snapshotHash) &&
    typeof value.invalidated === 'boolean' &&
    (value.snapshot === null || isSnapshot(value.snapshot)) &&
    (value.published === null || isPublished(value.published))
  )
}
function isOperation(value: unknown): value is DecisionOperation {
  return (
    isObject(value) &&
    typeof value.operationId === 'string' &&
    ((value.operationState === 'SUCCEEDED' &&
      hasStrings(value, [
        'recordId',
        'name',
        'displayName',
        'recordVersion',
        'status',
        'ownerTeam',
        'submissionId',
        'snapshotHash',
      ]) &&
      isStringOrNull(value.publishedVersion) &&
      isCatalogRecordType(value.recordType)) ||
      ((value.operationState === 'VERIFYING' || value.operationState === 'FAILED') &&
        typeof value.message === 'string'))
  )
}

export function createReviewApi(request: ApiRequest, invalidResponse: () => Error): ReviewApi {
  async function checked<T>(response: Promise<unknown>, guard: (value: unknown) => value is T): Promise<T> {
    const value = await response
    if (!guard(value)) throw invalidResponse()
    return value
  }
  return {
    listReviews: (token) =>
      checked(
        request(
          'GET',
          `/api/reviews${token === undefined ? '' : `?${new URLSearchParams({ nextToken: token })}`}`,
        ),
        isPage,
      ),
    getReview: (id) => checked(request('GET', `/api/reviews/${encodeURIComponent(id)}`), isDetail),
    approveReview: (id, input, key) =>
      checked(
        request('POST', `/api/reviews/${encodeURIComponent(id)}/approve`, input, {
          'idempotency-key': key,
        }),
        isOperation,
      ),
    rejectReview: (id, input, key) =>
      checked(
        request('POST', `/api/reviews/${encodeURIComponent(id)}/reject`, input, {
          'idempotency-key': key,
        }),
        isOperation,
      ),
    getDecisionOperation: (id) =>
      checked(request('GET', `/api/operations/${encodeURIComponent(id)}`), isOperation),
  }
}
