// 審査と決裁（Seam 2: SPA の fetch 境界）。Curator が審査待ちの一覧と詳細を見て、公開承認・却下を行う。
// バックエンドの review_queue.py と curation.py と契約を揃える。クライアントから AWS の status は送らない。
// 公開停止・廃止・廃止依頼の見送りは lifecycle/api.ts が受け持つ。
import { hasStrings, isObject, isPrincipal, type Principal } from '../api/guards'
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import { createResponseChecker, type ApiRequest } from '../api/request'
import {
  isDeprecationRequest,
  isRecordStatus,
  isRecoveryRecord,
  isUnsettledOperationState,
  type DeprecationRequest,
  type OperationState,
  type RecordStatus,
  type RecoveryRecord,
  type UnsettledOperationState,
} from '../drafts/api'

/** 決裁の種別。API の path（/approve・/reject）で AWS の遷移先が決まる（バックエンドの DECISION_TARGETS） */
export const DECISION_KINDS = ['approve', 'reject'] as const
export type DecisionKind = (typeof DECISION_KINDS)[number]

/**
 * 審査待ちの一覧の項目の種別。申請（AWS の承認待ち）と廃止依頼（ポータル内で発生）を並べて表示する
 * （設計書「API」。バックエンドの REVIEW_KINDS（domain/curation.py）と揃える）
 */
export const REVIEW_KINDS = ['SUBMISSION', 'DEPRECATION_REQUEST'] as const
export type ReviewKind = (typeof REVIEW_KINDS)[number]
function isReviewKind(value: unknown): value is ReviewKind {
  return REVIEW_KINDS.some((kind) => kind === value)
}

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

/** 一覧の項目に共通する、レコードの要約（バックエンドの CurationReads.record_summary） */
type ReviewRecordSummary = {
  recordId: string
  name: string
  displayName: string
  recordType: CatalogRecordType
  recordVersion: string
  status: RecordStatus
  managed: boolean
  ownerTeam: string | null
  /** レコードのロックを持つ操作。結果確認中の間は値を持つ */
  operationId: string | null
}

/** 申請の項目。AWS の承認待ちを正本とし、申請の補助情報を結合したもの */
export type SubmissionSummary = ReviewRecordSummary & {
  reviewKind: Extract<ReviewKind, 'SUBMISSION'>
  submissionState: AttachmentState
  submissionId: string | null
  snapshotHash: string | null
  reason: string | null
  submittedAt: string | null
  submittedBy: Principal | null
  invalidated: boolean
}

/** 未解決の廃止依頼の項目。補助テーブルが正本で、一覧の最初のページにだけ古い順に並ぶ */
export type DeprecationRequestSummary = ReviewRecordSummary & {
  reviewKind: Extract<ReviewKind, 'DEPRECATION_REQUEST'>
  requestId: string
  reason: string
  requestedAt: string
  requestedBy: Principal
  /** 公開停止済みで、廃止するか見送るかの判断を待っている */
  suspended: boolean
}

export type ReviewItem = SubmissionSummary | DeprecationRequestSummary

export type PendingPage = { items: ReviewItem[]; nextToken: string | null }

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
  /** 未解決の廃止依頼。依頼を出した Publisher の再申請を承認するときの見落としを防ぐ */
  deprecationRequest: DeprecationRequest | null
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
      /** 公開停止・廃止は申請を対象としないため null */
      submissionId: string | null
      snapshotHash: string | null
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

function isRecordSummary(value: Record<string, unknown>): boolean {
  return (
    hasStrings(value, ['recordId', 'name', 'displayName', 'recordVersion']) &&
    isCatalogRecordType(value.recordType) &&
    isRecordStatus(value.status) &&
    typeof value.managed === 'boolean' &&
    isStringOrNull(value.ownerTeam) &&
    isStringOrNull(value.operationId)
  )
}
function isSubmissionSummary(value: Record<string, unknown>): boolean {
  return (
    isAttachmentState(value.submissionState) &&
    isStringOrNull(value.submissionId) &&
    isStringOrNull(value.snapshotHash) &&
    isStringOrNull(value.reason) &&
    isStringOrNull(value.submittedAt) &&
    (value.submittedBy === null || isPrincipal(value.submittedBy)) &&
    typeof value.invalidated === 'boolean'
  )
}
function isDeprecationRequestSummary(value: Record<string, unknown>): boolean {
  return (
    hasStrings(value, ['requestId', 'reason', 'requestedAt']) &&
    isPrincipal(value.requestedBy) &&
    typeof value.suspended === 'boolean'
  )
}
/** 一覧の項目。種別（reviewKind）ごとに必須の項目が異なる（バックエンドの ReviewQueue と揃える） */
function isReviewItem(value: unknown): value is ReviewItem {
  if (!isObject(value) || !isReviewKind(value.reviewKind) || !isRecordSummary(value)) return false
  return value.reviewKind === 'SUBMISSION'
    ? isSubmissionSummary(value)
    : isDeprecationRequestSummary(value)
}
function isPage(value: unknown): value is PendingPage {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isReviewItem) &&
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
    (value.published === null || isPublished(value.published)) &&
    (value.deprecationRequest === null || isDeprecationRequest(value.deprecationRequest))
  )
}
/** 決裁 handler の操作の応答。決裁と公開停止・廃止（lifecycle/api.ts）で同じ形で返る */
export function isDecisionOperation(value: unknown): value is DecisionOperation {
  return (
    isObject(value) &&
    typeof value.operationId === 'string' &&
    ((value.operationState === 'SUCCEEDED' &&
      hasStrings(value, ['recordId', 'name', 'displayName', 'recordVersion', 'ownerTeam']) &&
      // 公開停止・廃止は申請を対象としないため、申請の情報は持たない
      isStringOrNull(value.submissionId) &&
      isStringOrNull(value.snapshotHash) &&
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
        isDecisionOperation,
      ),
    getDecisionOperation: (id) =>
      checked(request('GET', `/api/operations/${encodeURIComponent(id)}`), isDecisionOperation),
  }
}
