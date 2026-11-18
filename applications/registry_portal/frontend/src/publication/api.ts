// 公開管理（Seam 2: SPA の fetch 境界）。Curator の公開停止・廃止・廃止依頼の見送りと、その判断に使う照会・検索。
// バックエンドの publication.py・curation.py・deprecation.py と契約を揃える。操作の本文には AWS の status を
// 送らない（検索条件の状態（SUSPENDED と AWS の状態の語彙）は、絞り込みのためだけに送る）。
import { isObject } from '../api/guards'
import { createResponseChecker, type ApiRequest } from '../api/request'
import {
  AWS_RECORD_STATUSES,
  isDeprecationRequest,
  type DeprecationRequest,
  type RecordStatus,
} from '../drafts/api'
import {
  isDecisionOperation,
  isRecordSummary,
  type DecisionOperation,
  type RecordSummary,
} from '../reviews/api'

/**
 * 公開停止・廃止。API の path（/api/records/{recordId}/suspend・/deprecate）と、照会の actions のキー。
 * バックエンドの PUBLICATION_ACTIONS（domain/curation.py）と揃える
 */
export const PUBLICATION_ACTIONS = ['suspend', 'deprecate'] as const
export type PublicationAction = (typeof PUBLICATION_ACTIONS)[number]

/**
 * 管理レコード検索の状態の「公開停止中」。AWS のレコードの状態ではなく、ポータルが管理情報の
 * suspendedVersion で区別する値。REJECTED（却下済み）には公開停止中を含まない（Issue #42 の API 契約）
 */
export const SUSPENDED_SEARCH_STATUS = 'SUSPENDED' as const
/**
 * 管理レコード検索で選べる状態。公開停止中を先頭に、AWS の状態の語彙（DRAFT など9値）を続ける。
 * バックエンドの PUBLICATION_SEARCH_STATUSES（domain/curation.py）と値も並びも揃える
 */
export type PublicationSearchStatus = RecordStatus | typeof SUSPENDED_SEARCH_STATUS
export const PUBLICATION_SEARCH_STATUSES: readonly PublicationSearchStatus[] = [
  SUSPENDED_SEARCH_STATUS,
  ...AWS_RECORD_STATUSES,
]
export function isPublicationSearchStatus(value: unknown): value is PublicationSearchStatus {
  return PUBLICATION_SEARCH_STATUSES.some((status) => status === value)
}

/** いま実行できるか。できなければ、その理由（利用者に見せる文言）を持つ */
export type ActionAvailability = { allowed: boolean; message: string | null }

/** 公開管理の照会と管理レコードの検索が、レコードの要約に加える公開の状態 */
export type PublicationState = {
  /** 公開中の版（GLOSSARY「公開中の版」）。公開停止中と廃止済みでは null */
  publishedVersion: string | null
  /** 公開停止中（再承認まで。廃止済みは含めない） */
  suspended: boolean
}

/** 公開管理の照会の結果。公開停止・廃止・見送りを判断する前に確認する状態 */
export type PublicationView = RecordSummary &
  PublicationState & {
  /** レコードのロックを持つ操作（結果確認中など） */
  operationId: string | null
  /** 未解決の廃止依頼 */
  deprecationRequest: DeprecationRequest | null
  actions: Record<PublicationAction, ActionAvailability>
}

/** 公開停止の入力。バックエンドの PublicationInput（SUSPEND）と揃える */
export type SuspendInput = { reason: string }
/** 廃止の入力。元に戻せないため確認（confirm）が必須（バックエンドの PublicationInput（DEPRECATE）） */
export type DeprecateInput = { reason: string; confirm: true }
/** 見送りの入力。画面で確認した依頼（requestId）と一致するときだけ見送る */
export type DeclineInput = { requestId: string; reason: string }

/** 管理レコード検索の条件（GET /api/records/publication）。name は部分一致、status は省略可（すべて） */
export type PublicationSearchRequest = {
  name: string
  status: PublicationSearchStatus | null
  /** バックエンドが中継する AWS の値で、SPA では中身を解釈しない */
  nextToken?: string
}

/**
 * 管理レコード検索の項目。record_summary に公開の状態を加えた形（Issue #41・#42 の API 契約）。
 * 検索は管理下のレコードだけを返すため managed は常に true だが、照会・審査と同じ要約の形を保つ
 */
export type PublicationRecordSummary = RecordSummary & PublicationState

/** 1ページ分の検索結果。管理下のレコードだけを返すため、ポータル側の絞り込みで items が0件でも nextToken があり得る */
export type PublicationRecordPage = { items: PublicationRecordSummary[]; nextToken: string | null }

export type PublicationApi = {
  /** 管理下のレコードを名前の一部と状態で探す（Curator のみ。所有チームでは絞り込まない） */
  searchPublicationRecords: (input: PublicationSearchRequest) => Promise<PublicationRecordPage>
  getPublication: (recordId: string) => Promise<PublicationView>
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

function isPublicationState(value: Record<string, unknown>): boolean {
  return (
    (value.publishedVersion === null || typeof value.publishedVersion === 'string') &&
    typeof value.suspended === 'boolean'
  )
}

function isPublicationView(value: unknown): value is PublicationView {
  return (
    isObject(value) &&
    isRecordSummary(value) &&
    isPublicationState(value) &&
    (value.operationId === null || typeof value.operationId === 'string') &&
    (value.deprecationRequest === null || isDeprecationRequest(value.deprecationRequest)) &&
    isObject(value.actions) &&
    PUBLICATION_ACTIONS.every((action) => isAvailability((value.actions as Record<string, unknown>)[action]))
  )
}

function isPublicationRecordSummary(value: unknown): value is PublicationRecordSummary {
  return isObject(value) && isRecordSummary(value) && isPublicationState(value)
}

function isPublicationRecordPage(value: unknown): value is PublicationRecordPage {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isPublicationRecordSummary) &&
    (value.nextToken === null || typeof value.nextToken === 'string')
  )
}

export function createPublicationApi(request: ApiRequest, invalidResponse: () => Error): PublicationApi {
  const checked = createResponseChecker(invalidResponse)
  const recordPath = (id: string) => `/api/records/${encodeURIComponent(id)}`
  // 公開停止・廃止の path は PUBLICATION_ACTIONS の値から組み立て、値の定義を一か所に保つ
  const sendAction =
    (action: PublicationAction) =>
    (id: string, input: SuspendInput | DeprecateInput, key: string) =>
      checked(
        request('POST', `${recordPath(id)}/${action}`, input, { 'idempotency-key': key }),
        isDecisionOperation,
      )
  return {
    searchPublicationRecords: (input) => {
      // 空の条件はクエリ文字列に含めない（name なし = すべて、status なし = すべて）
      const query = new URLSearchParams()
      if (input.name !== '') query.set('name', input.name)
      if (input.status !== null) query.set('status', input.status)
      if (input.nextToken !== undefined) query.set('nextToken', input.nextToken)
      const suffix = query.toString()
      return checked(
        request('GET', `/api/records/publication${suffix === '' ? '' : `?${suffix}`}`),
        isPublicationRecordPage,
      )
    },
    getPublication: (id) => checked(request('GET', `${recordPath(id)}/publication`), isPublicationView),
    suspendRecord: sendAction('suspend'),
    deprecateRecord: sendAction('deprecate'),
    declineDeprecationRequest: (id, input) =>
      checked(
        request('POST', `${recordPath(id)}/deprecation-requests/decline`, input),
        isDeprecationRequest,
      ),
  }
}
