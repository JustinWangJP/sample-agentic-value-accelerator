import type { Role } from '../api/client'
import { UNSET_DATA_CLASSIFICATION, isDataClassification } from '../api/dataClassification'
import { hasStrings, isObject, isPrincipal, type Principal } from '../api/guards'
import { isCatalogRecordType, type CatalogRecordType } from '../api/recordType'
import { createResponseChecker, type ApiRequest } from '../api/request'
import {
  COMMON_TEXT_FIELDS,
  DESCRIPTOR_FIELD_SPECS,
  isAllowedKind,
  isDescriptorType,
  type RecordDraft,
} from './descriptors'

export type { Principal, RecordDraft }

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
/** 廃止済み。元に戻せない終端の状態。バックエンドの DEPRECATED_STATUS と揃える */
export const DEPRECATED_STATUS: RecordStatus = 'DEPRECATED'

/** 画面に出す AWS の状態。PENDING_APPROVAL は「審査待ち」と表示する（GLOSSARY「申請」） */
export function recordStatusLabel(status: RecordStatus): string {
  return status === PENDING_APPROVAL_STATUS ? '審査待ち' : status
}

/**
 * 決裁・公開停止・廃止の理由の上限。AWS の UpdateRegistryRecordStatus の statusReason（0〜255文字）に合わせる
 * （バックエンドの REASON_MAX_LENGTH（domain/curation.py））
 */
export const STATUS_REASON_MAX_LENGTH = 255
/** 編集を受け付けない状態。バックエンドの NOT_EDITABLE_STATUSES と揃える */
const NOT_EDITABLE_STATUSES: readonly RecordStatus[] = ['CREATING', 'UPDATING', DEPRECATED_STATUS]
export function isEditableStatus(status: RecordStatus): boolean {
  return !NOT_EDITABLE_STATUSES.includes(status)
}
export type DraftDetail = {
  recordId: string
  draft: RecordDraft
  contentHash: string
  initializationNotice?: string
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
/** 回復の方法。RECONCILED は Registry の再取得で確定、RELEASED は確定できないまま強制解除（バックエンドの RecoveryMode） */
export const RECOVERY_MODES = ['RECONCILED', 'RELEASED'] as const
export type RecoveryMode = (typeof RECOVERY_MODES)[number]
/**
 * Admin の回復操作が確定・強制解除した操作の記録（回復した主体・時刻・理由・方法）。
 * バックエンドの RecoveryRecord・is_recovery_record（domain/drafts.py）と揃える。
 * 操作照会・履歴・回復の応答に同じ形で載る
 */
export type RecoveryRecord = {
  mode: RecoveryMode
  by: Principal
  at: string
  reason: string
}
export function isRecoveryRecord(value: unknown): value is RecoveryRecord {
  return (
    isObject(value) &&
    RECOVERY_MODES.some((mode) => mode === value.mode) &&
    isPrincipal(value.by) &&
    hasStrings(value, ['at', 'reason'])
  )
}
/** 回復結果は、回復操作で確定した操作にだけ付く */
function hasOptionalRecovery(value: Record<string, unknown>): boolean {
  return value.recovery === undefined || isRecoveryRecord(value.recovery)
}
export type DraftOperation =
  | (DraftDetail & {
      operationId: string
      operationState: Extract<OperationState, 'SUCCEEDED'>
      recovery?: RecoveryRecord
    })
  | {
      operationId: string
      operationState: UnsettledOperationState
      message: string
      recovery?: RecoveryRecord
    }

/** 操作の種別。バックエンドの OperationKind（domain/drafts.py）と揃える */
export const OPERATION_KINDS = [
  'CREATE',
  'UPDATE',
  'SUBMIT',
  'APPROVE',
  'REJECT',
  'SUSPEND',
  'DEPRECATE',
  'REQUEST_DEPRECATION',
  'DECLINE_DEPRECATION',
  'ADOPT',
  'REASSIGN_OWNER',
] as const
export type OperationKind = (typeof OPERATION_KINDS)[number]
/** 操作と審査の種別の表示 */
export const KIND_LABELS: Record<OperationKind, string> = {
  CREATE: '作成',
  UPDATE: '編集',
  SUBMIT: '申請',
  APPROVE: '公開承認',
  REJECT: '却下',
  SUSPEND: '公開停止',
  DEPRECATE: '廃止',
  REQUEST_DEPRECATION: '廃止依頼',
  DECLINE_DEPRECATION: '廃止依頼の見送り',
  ADOPT: '取り込み',
  REASSIGN_OWNER: '所有チーム変更',
}
/**
 * 履歴 API が返す操作記録の結果。バックエンドの HistoryState（domain/drafts.py）と揃える。
 * 書き込み操作の応答の状態（OPERATION_STATES）に、取り込み前の公開承認の UNKNOWN（不明）を加えたもの。
 * 結果の追記記録がない操作は、操作照会と同じ VERIFYING（結果確認中）で表す
 */
export const HISTORY_STATES = [...OPERATION_STATES, 'UNKNOWN'] as const
export type HistoryState = (typeof HISTORY_STATES)[number]
/** 操作の結果の表示。結果の追記記録がない操作は「結果確認中」（GLOSSARY「結果確認中」）。履歴と回復操作の画面で使う */
export const STATE_LABELS: Record<HistoryState, string> = {
  SUCCEEDED: '成功',
  FAILED: '失敗',
  VERIFYING: '結果確認中',
  UNKNOWN: '不明',
}
/**
 * 操作と審査の履歴の1項目。バックエンドの history.py と揃える。
 * principal が null で state が UNKNOWN の項目は、取り込み前から公開されていた版の公開承認（決裁した主体が不明）。
 * withheld が true の項目は結果確認中の決裁で、決裁した Curator 以外には操作 ID・理由・対象のハッシュと版を返さない（ADR-0010）
 */
export type HistoryEntry = {
  operationId: string | null
  kind: OperationKind
  principal: Principal | null
  startedAt: string | null
  targetHash: string | null
  state: HistoryState
  awsRequestId?: string
  errorCode?: string
  reason?: string
  recordVersion?: string
  withheld?: true
  recovery?: RecoveryRecord
}
/**
 * 所有チームによらず、すべての Registry レコードの操作履歴を読める業務ロール（Curator・Admin）か。
 * バックエンドの history.py の `_is_privileged` と揃える
 */
export function canViewAllHistory(roles: readonly Role[]): boolean {
  return roles.includes('Curator') || roles.includes('Admin')
}
export type RecordHistory = {
  recordId: string
  ownerTeam: string
  entries: HistoryEntry[]
}

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
  getHistory: (recordId: string) => Promise<RecordHistory>
  /** 廃止依頼。AWS の状態は変えず、Curator が審査待ちの一覧で廃止するか見送るかを判断する */
  requestDeprecation: (recordId: string, input: DeprecationRequestInput) => Promise<DeprecationRequest>
}

/** 廃止依頼・見送りの理由の上限。バックエンドの DEPRECATION_REASON_MAX_LENGTH（domain/deprecation.py）と揃える */
export const DEPRECATION_REASON_MAX_LENGTH = 255
/** 廃止依頼の入力 */
export type DeprecationRequestInput = { reason: string }
/** 廃止依頼の状態。バックエンドの DeprecationRequestState（domain/deprecation.py）と揃える */
export const DEPRECATION_REQUEST_STATES = ['OPEN', 'RESOLVED'] as const
export type DeprecationRequestState = (typeof DEPRECATION_REQUEST_STATES)[number]
/**
 * 解決済みの依頼の終わり方。廃止（Curator の廃止で解決）か見送り（理由を付けて閉じる）だけ。
 * 公開停止は依頼を閉じない。バックエンドの DeprecationResolution（domain/deprecation.py）と揃える
 */
export const DEPRECATION_RESOLUTIONS = ['DEPRECATED', 'DECLINED'] as const
export type DeprecationResolution = (typeof DEPRECATION_RESOLUTIONS)[number]
/** 廃止依頼。バックエンドの DeprecationRequest.view（domain/deprecation.py）と揃える */
export type DeprecationRequest = {
  state: DeprecationRequestState
  requestId: string
  recordId: string
  reason: string
  requestedBy: Principal
  requestedAt: string
  resolution: DeprecationResolution | null
  /** 見送りでは見送りの理由、廃止では廃止の理由 */
  resolutionReason: string | null
  resolvedBy: Principal | null
  resolvedAt: string | null
}
export function isDeprecationRequest(value: unknown): value is DeprecationRequest {
  return (
    isObject(value) &&
    DEPRECATION_REQUEST_STATES.some((state) => state === value.state) &&
    hasStrings(value, ['requestId', 'recordId', 'reason', 'requestedAt']) &&
    isPrincipal(value.requestedBy) &&
    (value.resolution === null || DEPRECATION_RESOLUTIONS.some((kind) => kind === value.resolution)) &&
    (value.resolutionReason === null || typeof value.resolutionReason === 'string') &&
    (value.resolvedBy === null || isPrincipal(value.resolvedBy)) &&
    (value.resolvedAt === null || typeof value.resolvedAt === 'string')
  )
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
    (value.dataClassification === UNSET_DATA_CLASSIFICATION || isDataClassification(value.dataClassification))
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
    (value.initializationNotice === undefined || typeof value.initializationNotice === 'string') &&
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
      (isUnsettledOperationState(value.operationState) && typeof value.message === 'string')) &&
    hasOptionalRecovery(value)
  )
}
function isHistoryEntry(value: unknown): value is HistoryEntry {
  return (
    isObject(value) &&
    (value.operationId === null || typeof value.operationId === 'string') &&
    OPERATION_KINDS.some((kind) => kind === value.kind) &&
    (value.principal === null || isPrincipal(value.principal)) &&
    (value.startedAt === null || typeof value.startedAt === 'string') &&
    (value.targetHash === null || typeof value.targetHash === 'string') &&
    HISTORY_STATES.some((state) => state === value.state) &&
    (value.awsRequestId === undefined || typeof value.awsRequestId === 'string') &&
    (value.errorCode === undefined || typeof value.errorCode === 'string') &&
    (value.reason === undefined || typeof value.reason === 'string') &&
    (value.recordVersion === undefined || typeof value.recordVersion === 'string') &&
    (value.withheld === undefined || value.withheld === true) &&
    hasOptionalRecovery(value)
  )
}
function isHistory(value: unknown): value is RecordHistory {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'ownerTeam']) &&
    Array.isArray(value.entries) &&
    value.entries.every(isHistoryEntry)
  )
}

export function createDraftApi(request: ApiRequest, invalidResponse: () => Error): DraftApi {
  const checked = createResponseChecker(invalidResponse)
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
    getHistory: (id) =>
      checked(
        request('GET', `/api/records/${encodeURIComponent(id)}/history`),
        isHistory,
      ),
    requestDeprecation: (id, input) =>
      checked(
        request('POST', `/api/records/${encodeURIComponent(id)}/deprecation-requests`, input),
        isDeprecationRequest,
      ),
  }
}
