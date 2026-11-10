// 審査と決裁（Seam 2: SPA の fetch 境界）。Curator が審査待ちの一覧と詳細を見て、公開承認・却下を行う。
// バックエンドの curation.py と契約を揃える。クライアントから AWS の status は送らない。
import { hasStrings, isObject, isPrincipal, type Principal } from '../api/guards'
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import { createResponseChecker, type ApiRequest } from '../api/request'
import {
  isRecordStatus,
  isRecoveryRecord,
  isUnsettledOperationState,
  type OperationState,
  type RecordStatus,
  type RecoveryRecord,
  type UnsettledOperationState,
} from '../drafts/api'

/** 決裁理由の上限。AWS の statusReason（0〜255文字）に合わせる（バックエンドの REASON_MAX_LENGTH） */
export const DECISION_REASON_MAX_LENGTH = 255

/** 決裁の種別。API の path（/approve・/reject）で AWS の遷移先が決まる（バックエンドの DECISION_TARGETS） */
export const DECISION_KINDS = ['approve', 'reject'] as const
export type DecisionKind = (typeof DECISION_KINDS)[number]

/**
 * 申請スナップショット・公開版スナップショットの取得状態。バックエンドの AttachmentState（domain/curation.py）と揃える。
 * NONE はそもそも存在しない（ポータル外の申請・初回公開）、MISSING は存在するはずなのに読めない（保存内容の異常）
 */
export const ATTACHMENT_STATES = ['NONE', 'AVAILABLE', 'MISSING'] as const
export type AttachmentState = (typeof ATTACHMENT_STATES)[number]
function isAttachmentState(value: unknown): value is AttachmentState {
  return ATTACHMENT_STATES.some((state) => state === value)
}

export type RecordReference = { recordId: string; recordVersion: string }

export type { Principal }

/** 審査待ちの一覧の項目。AWS の承認待ちを正本とし、申請の補助情報を結合したもの */
export type PendingSummary = {
  recordId: string
  name: string
  displayName: string
  recordType: CatalogRecordType
  recordVersion: string
  status: RecordStatus
  managed: boolean
  ownerTeam: string | null
  submissionState: AttachmentState
  submissionId: string | null
  snapshotHash: string | null
  reason: string | null
  submittedAt: string | null
  submittedBy: Principal | null
  invalidated: boolean
  operationId: string | null
}

export type PendingPage = { items: PendingSummary[]; nextToken: string | null }

/**
 * 差分の比較対象（公開中の版のスナップショット）。ADR-0007 の公開版スナップショットから読む。
 * 定義（record）と承認者（approvedBy）を記録する前のスナップショットでは null
 */
export type PublishedBase = {
  recordVersion: string
  contact: string | null
  termsOfUse: string | null
  connectionReference: string | null
  relatedReferences: RecordReference[]
  record: Record<string, unknown> | null
  approvedBy: Principal | null
}

/** 申請のスナップショット。Curator が審査し、決裁時にハッシュで固定する対象 */
export type SubmissionSnapshot = {
  submissionId: string
  submittedBy: Principal
  lastEditedBy: Principal
  submittedAt: string
  reason: string
  record: Record<string, unknown>
  ownerTeam: string
  supplement: Record<string, unknown>
  relatedReferences: RecordReference[]
  contact: string
  termsOfUse: string
  connectionReference: string
  contentHash: string
  snapshotHash: string
}

export type PendingDetail = {
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
  submissionState: AttachmentState
  submissionId: string | null
  snapshotHash: string | null
  /** 保存後にスナップショットが書き換えられていれば false。決裁は 409 で拒否される */
  snapshotIntact: boolean
  invalidated: boolean
  snapshot: SubmissionSnapshot | null
  publishedState: AttachmentState
  published: PublishedBase | null
}

/** 決裁の結果。SUCCEEDED は決裁が AWS に反映され、公開版スナップショットも保存済み */
export type DecisionOperation =
  | {
      recordId: string
      name: string
      displayName: string
      recordType: CatalogRecordType
      recordVersion: string
      status: RecordStatus
      ownerTeam: string
      publishedVersion: string | null
      operationId: string
      operationState: Extract<OperationState, 'SUCCEEDED'>
      submissionId: string
      snapshotHash: string
      /** Admin の回復操作で確定した決裁にだけ付く */
      recovery?: RecoveryRecord
    }
  | {
      operationId: string
      operationState: UnsettledOperationState
      message: string
      recovery?: RecoveryRecord
    }

export type DecisionInput = { snapshotHash: string; reason: string }

export type ReviewApi = {
  listReviews: (nextToken?: string) => Promise<PendingPage>
  getReview: (recordId: string) => Promise<PendingDetail>
  decideSubmission: (
    kind: DecisionKind,
    recordId: string,
    input: DecisionInput,
    idempotencyKey: string,
  ) => Promise<DecisionOperation>
  getDecisionOperation: (operationId: string) => Promise<DecisionOperation>
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}
function isReference(value: unknown): value is RecordReference {
  return isObject(value) && hasStrings(value, ['recordId', 'recordVersion'])
}
function isReferenceList(value: unknown): value is RecordReference[] {
  return Array.isArray(value) && value.every(isReference)
}

function isSummary(value: unknown): value is PendingSummary {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) &&
    isRecordStatus(value.status) &&
    typeof value.managed === 'boolean' &&
    isStringOrNull(value.ownerTeam) &&
    isAttachmentState(value.submissionState) &&
    isStringOrNull(value.submissionId) &&
    isStringOrNull(value.snapshotHash) &&
    isStringOrNull(value.reason) &&
    isStringOrNull(value.submittedAt) &&
    (value.submittedBy === null || isPrincipal(value.submittedBy)) &&
    typeof value.invalidated === 'boolean' &&
    isStringOrNull(value.operationId)
  )
}
function isPage(value: unknown): value is PendingPage {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isSummary) &&
    isStringOrNull(value.nextToken)
  )
}
function isPublished(value: unknown): value is PublishedBase {
  return (
    isObject(value) &&
    hasStrings(value, ['recordVersion']) &&
    isStringOrNull(value.contact) &&
    isStringOrNull(value.termsOfUse) &&
    isStringOrNull(value.connectionReference) &&
    isReferenceList(value.relatedReferences) &&
    (value.record === null || isObject(value.record)) &&
    (value.approvedBy === null || isPrincipal(value.approvedBy))
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
function isDetail(value: unknown): value is PendingDetail {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'description', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) &&
    isRecordStatus(value.status) &&
    typeof value.managed === 'boolean' &&
    isStringOrNull(value.ownerTeam) &&
    isStringOrNull(value.publishedVersion) &&
    isAttachmentState(value.submissionState) &&
    isStringOrNull(value.submissionId) &&
    isStringOrNull(value.snapshotHash) &&
    typeof value.snapshotIntact === 'boolean' &&
    typeof value.invalidated === 'boolean' &&
    (value.snapshot === null || isSnapshot(value.snapshot)) &&
    isAttachmentState(value.publishedState) &&
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
        'ownerTeam',
        'submissionId',
        'snapshotHash',
      ]) &&
      isRecordStatus(value.status) &&
      isStringOrNull(value.publishedVersion) &&
      isCatalogRecordType(value.recordType)) ||
      (isUnsettledOperationState(value.operationState) && typeof value.message === 'string')) &&
    (value.recovery === undefined || isRecoveryRecord(value.recovery))
  )
}

export function createReviewApi(request: ApiRequest, invalidResponse: () => Error): ReviewApi {
  const checked = createResponseChecker(invalidResponse)
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
    decideSubmission: (kind, id, input, key) =>
      checked(
        request('POST', `/api/reviews/${encodeURIComponent(id)}/${kind}`, input, {
          'idempotency-key': key,
        }),
        isOperation,
      ),
    getDecisionOperation: (id) =>
      checked(request('GET', `/api/operations/${encodeURIComponent(id)}`), isOperation),
  }
}
