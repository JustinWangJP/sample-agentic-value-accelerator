import { createDraftApi, type DraftApi } from '../drafts/api'
import { createRecoveryApi, type RecoveryApi } from '../recovery/api'
import { createReviewApi, type ReviewApi } from '../reviews/api'
import { hasStrings, isObject } from './guards'
import { isDataClassification, type DataClassification } from './dataClassification'
import {
  AGENT_RECORD_TYPE,
  CATALOG_RECORD_TYPES,
  isCatalogRecordType,
  type CatalogRecordType,
} from './recordType'
import type { ApiRequest, HttpMethod } from './request'

// Registry ポータル API のクライアント
//
// - アクセストークン（ID トークンではない）を Bearer で送る
// - エラーは共通エラー DTO の日本語メッセージを使い、ApiError として返す
// - JSON でない応答（SPA の index.html など）を成功として扱わない
// - 401 は、Lambda の共通エラー DTO でも、API Gateway の JWT authorizer の応答
//   （{"message":"Unauthorized"}）でも、同じ「ログインし直す」エラーとして扱う

export type Principal = {
  type: 'human'
  issuer: string
  subject: string
}

export type Role = 'Viewer' | 'Publisher' | 'Curator' | 'Admin'

export type Me = {
  principal: Principal
  roles: Role[]
  teams: string[]
}

export { CATALOG_RECORD_TYPES, type CatalogRecordType }

/** A2A は Agent の descriptor なので、A2A の絞り込みは Agent（または種別の指定なし）にだけ使える */
export function supportsA2AFilter(type: CatalogRecordType | null): boolean {
  return type === null || type === AGENT_RECORD_TYPE
}

/** カタログ項目（公開承認済みの Registry レコードの版）の要約 */
export type CatalogItem = {
  recordId: string
  name: string
  displayName: string
  description: string
  recordType: CatalogRecordType
  descriptorTypes: string[]
  isA2A: boolean
  recordVersion: string
  updatedAt: string
}

/** 1ページ分のカタログ項目。nextToken が null なら最後のページ */
export type CatalogPage = {
  items: CatalogItem[]
  nextToken: string | null
}

/** 検索条件。query は必須（1〜256文字）。条件だけで探す場合は一覧を使う */
export type CatalogSearchRequest = {
  query: string
  type?: CatalogRecordType
  a2a?: boolean
  dataClassification?: DataClassification
}

export type CatalogSearchResult = {
  items: CatalogItem[]
  /** A2A 検索用 metadata と定義（descriptor）が食い違うため除外した件数 */
  inconsistentA2ACount: number
}

export type CatalogReference = { recordId: string; recordVersion: string }

/**
 * カタログ項目の詳細。
 * 連絡先・利用条件・接続先の参照・関連参照は、補助テーブルの公開版スナップショットから読む。
 * null はスナップショットを取得できなかったこと（未取得）、空文字は値がないこと（未登録）を表す
 */
export type CatalogDetail = CatalogItem & {
  definition: Record<string, unknown>
  ownerTeam: string
  contact: string | null
  termsOfUse: string | null
  connectionReference: string | null
  dataClassification: DataClassification | null
  skillMarkdown: string
  relatedReferences: CatalogReference[] | null
}

/** A2A 検索用 metadata の準備が終わっておらず、A2A の絞り込み検索を受け付けないときのエラーコード */
export const A2A_SEARCH_NOT_READY = 'A2A_SEARCH_NOT_READY'

/** 認証が必要なときのメッセージ。バックエンドの AuthenticationError の既定文言と同じ */
export const UNAUTHENTICATED_MESSAGE = '認証が必要です。ログインし直してください。'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }

  /** ログインし直せば解決しうるエラーか（401。403 などは対象外） */
  get requiresLogin(): boolean {
    return this.code === 'UNAUTHENTICATED'
  }
}

/** 想定外の例外も含め、画面に出せる ApiError にそろえる */
export function toApiError(failure: unknown, fallbackMessage: string): ApiError {
  return failure instanceof ApiError ? failure : new ApiError(0, 'UNEXPECTED_ERROR', fallbackMessage)
}

type ErrorDto = { error: { code: string; message: string; requestId?: string | null } }

function isErrorDto(value: unknown): value is ErrorDto {
  if (typeof value !== 'object' || value === null) return false
  const error = (value as { error?: unknown }).error
  return (
    typeof error === 'object' &&
    error !== null &&
    typeof (error as { code?: unknown }).code === 'string' &&
    typeof (error as { message?: unknown }).message === 'string'
  )
}

function isCatalogItem(value: unknown): value is CatalogItem {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'description', 'recordVersion', 'updatedAt']) &&
    isCatalogRecordType(value.recordType) &&
    Array.isArray(value.descriptorTypes) &&
    value.descriptorTypes.every((type) => typeof type === 'string') &&
    typeof value.isA2A === 'boolean'
  )
}

function isCatalogReference(value: unknown): value is CatalogReference {
  return isObject(value) && hasStrings(value, ['recordId', 'recordVersion'])
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isCatalogSearchResult(value: unknown): value is CatalogSearchResult {
  return (
    isObject(value) &&
    Array.isArray(value.items) &&
    value.items.every(isCatalogItem) &&
    Number.isInteger(value.inconsistentA2ACount)
  )
}

function isCatalogDetail(value: unknown): value is CatalogDetail {
  return (
    isObject(value) &&
    isObject(value.definition) &&
    hasStrings(value, ['ownerTeam', 'skillMarkdown']) &&
    isStringOrNull(value.contact) &&
    isStringOrNull(value.termsOfUse) &&
    isStringOrNull(value.connectionReference) &&
    (value.dataClassification === null || isDataClassification(value.dataClassification)) &&
    (value.relatedReferences === null ||
      (Array.isArray(value.relatedReferences) && value.relatedReferences.every(isCatalogReference))) &&
    isCatalogItem(value)
  )
}

function invalidResponse(status: number): ApiError {
  return new ApiError(status, 'INVALID_RESPONSE', 'API から想定外の応答が返されました。')
}

export type ApiClientOptions = {
  baseUrl: string
  fetch: typeof fetch
  getAccessToken: () => string | null
}

export type ApiClient = DraftApi &
  ReviewApi &
  RecoveryApi & {
  getMe: () => Promise<Me>
  /** nextToken はバックエンドが中継する AWS の値で、SPA では中身を解釈しない */
  listCatalog: (
    type: CatalogRecordType,
    nextToken?: string,
    a2aOnly?: boolean,
  ) => Promise<CatalogPage>
  searchCatalog: (query: CatalogSearchRequest) => Promise<CatalogSearchResult>
  getCatalogDetail: (recordId: string) => Promise<CatalogDetail>
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const request: ApiRequest = async <T>(
    method: HttpMethod,
    path: string,
    payload?: unknown,
    extraHeaders: Record<string, string> = {},
  ): Promise<T> => {
    const token = options.getAccessToken()
    if (!token) {
      throw new ApiError(401, 'UNAUTHENTICATED', UNAUTHENTICATED_MESSAGE)
    }
    let response: Response
    try {
      // baseUrl の末尾の / は loadConfig で取り除き済み
      const headers = {
        authorization: `Bearer ${token}`,
        accept: 'application/json',
        ...extraHeaders,
      }
      response = await options.fetch(
        `${options.baseUrl}${path}`,
        method === 'GET'
          ? { headers }
          : {
              method,
              headers: { ...headers, 'content-type': 'application/json' },
              body: JSON.stringify(payload),
            },
      )
    } catch {
      throw new ApiError(
        0,
        'NETWORK_ERROR',
        'API に接続できません。ネットワークを確認してください。',
      )
    }

    const isJson = (response.headers.get('content-type') ?? '').includes('application/json')
    const body: unknown = isJson ? await response.json().catch(() => undefined) : undefined

    if (!response.ok) {
      if (isErrorDto(body)) {
        throw new ApiError(
          response.status,
          body.error.code,
          body.error.message,
          body.error.requestId ?? null,
        )
      }
      if (response.status === 401) {
        // API Gateway の JWT authorizer が拒否した応答（Lambda まで届かず、共通 DTO ではない）
        throw new ApiError(401, 'UNAUTHENTICATED', UNAUTHENTICATED_MESSAGE)
      }
      throw new ApiError(
        response.status,
        'UNEXPECTED_ERROR',
        `API の呼び出しに失敗しました（HTTP ${response.status}）。`,
      )
    }
    if (body === undefined) {
      throw invalidResponse(response.status)
    }
    return body as T
  }

  return {
    ...createDraftApi(request, () => invalidResponse(200)),
    ...createReviewApi(request, () => invalidResponse(200)),
    ...createRecoveryApi(request, () => invalidResponse(200)),
    getMe: () => request<Me>('GET', '/api/me'),
    listCatalog: (type, nextToken, a2aOnly) => {
      const query = new URLSearchParams({ type })
      if (supportsA2AFilter(type) && a2aOnly) query.set('a2a', 'true')
      if (nextToken !== undefined) query.set('nextToken', nextToken)
      return request<CatalogPage>('GET', `/api/catalog?${query.toString()}`)
    },
    searchCatalog: async (query) => {
      const body = await request<unknown>('POST', '/api/catalog/search', query)
      if (!isCatalogSearchResult(body)) throw invalidResponse(200)
      return body
    },
    getCatalogDetail: async (recordId) => {
      const body = await request<unknown>('GET', `/api/catalog/${encodeURIComponent(recordId)}`)
      if (!isCatalogDetail(body)) throw invalidResponse(200)
      return body
    },
  }
}
