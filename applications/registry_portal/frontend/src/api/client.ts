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

/**
 * カタログの種別（AWS の recordType）。A2A は Agent の descriptor として扱う。
 * バックエンドの CatalogRecordType（backend/src/registry_portal/domain/catalog.py）と同じ値に揃える
 */
export type CatalogRecordType = 'AGENT' | 'MCP' | 'SKILL'

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

export type CatalogSearchRequest = {
  query: string
  type?: CatalogRecordType
  a2a?: boolean
  dataClassification?: 'public' | 'private'
}

export type CatalogReference = { recordId: string; recordVersion: string }

export type CatalogDetail = CatalogItem & {
  definition: Record<string, unknown>
  ownerTeam: string
  contact: string
  termsOfUse: string
  connectionReference: string
  dataClassification: string
  skillMarkdown: string
  relatedReferences: CatalogReference[] | null
}

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

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasStrings(object: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof object[key] === 'string')
}

function isCatalogItem(value: unknown): value is CatalogItem {
  return (
    isObject(value) &&
    hasStrings(value, ['recordId', 'name', 'displayName', 'description', 'recordVersion', 'updatedAt']) &&
    (value.recordType === 'AGENT' || value.recordType === 'MCP' || value.recordType === 'SKILL') &&
    Array.isArray(value.descriptorTypes) &&
    value.descriptorTypes.every((type) => typeof type === 'string') &&
    typeof value.isA2A === 'boolean'
  )
}

function isCatalogReference(value: unknown): value is CatalogReference {
  return isObject(value) && hasStrings(value, ['recordId', 'recordVersion'])
}

function isCatalogDetail(value: unknown): value is CatalogDetail {
  return (
    isObject(value) &&
    isObject(value.definition) &&
    hasStrings(value, [
      'ownerTeam',
      'contact',
      'termsOfUse',
      'connectionReference',
      'dataClassification',
      'skillMarkdown',
    ]) &&
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

export type ApiClient = {
  getMe: () => Promise<Me>
  /** nextToken はバックエンドが中継する AWS の値で、SPA では中身を解釈しない */
  listCatalog: (
    type: CatalogRecordType,
    nextToken?: string,
    a2aOnly?: boolean,
  ) => Promise<CatalogPage>
  searchCatalog: (query: CatalogSearchRequest) => Promise<CatalogItem[]>
  getCatalogDetail: (recordId: string) => Promise<CatalogDetail>
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  async function request<T>(path: string, payload?: CatalogSearchRequest): Promise<T> {
    const token = options.getAccessToken()
    if (!token) {
      throw new ApiError(401, 'UNAUTHENTICATED', UNAUTHENTICATED_MESSAGE)
    }
    let response: Response
    try {
      // baseUrl の末尾の / は loadConfig で取り除き済み
      const headers = { authorization: `Bearer ${token}`, accept: 'application/json' }
      response = await options.fetch(
        `${options.baseUrl}${path}`,
        payload === undefined
          ? { headers }
          : {
              method: 'POST',
              headers: { ...headers, 'content-type': 'application/json' },
              body: JSON.stringify(payload),
            },
      )
    } catch {
      throw new ApiError(0, 'NETWORK_ERROR', 'API に接続できません。ネットワークを確認してください。')
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
    getMe: () => request<Me>('/api/me'),
    listCatalog: (type, nextToken, a2aOnly) => {
      const query = new URLSearchParams({ type })
      if (type === 'AGENT' && a2aOnly) query.set('a2a', 'true')
      if (nextToken !== undefined) query.set('nextToken', nextToken)
      return request<CatalogPage>(`/api/catalog?${query.toString()}`)
    },
    searchCatalog: async (query) => {
      const body = await request<unknown>('/api/catalog/search', query)
      if (!isObject(body) || !Array.isArray(body.items) || !body.items.every(isCatalogItem)) {
        throw invalidResponse(200)
      }
      return body.items
    },
    getCatalogDetail: async (recordId) => {
      const body = await request<unknown>(`/api/catalog/${encodeURIComponent(recordId)}`)
      if (!isCatalogDetail(body)) throw invalidResponse(200)
      return body
    },
  }
}
