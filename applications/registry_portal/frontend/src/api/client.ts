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

/** カタログの種別（AWS の recordType）。A2A は Agent の descriptor として扱う */
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

export type ApiClientOptions = {
  baseUrl: string
  fetch: typeof fetch
  getAccessToken: () => string | null
}

export type ApiClient = {
  getMe: () => Promise<Me>
  /** nextToken はバックエンドが中継する AWS の値で、SPA では中身を解釈しない */
  listCatalog: (type: CatalogRecordType, nextToken?: string) => Promise<CatalogPage>
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  async function request<T>(path: string): Promise<T> {
    const token = options.getAccessToken()
    if (!token) {
      throw new ApiError(401, 'UNAUTHENTICATED', UNAUTHENTICATED_MESSAGE)
    }
    let response: Response
    try {
      // baseUrl の末尾の / は loadConfig で取り除き済み
      response = await options.fetch(`${options.baseUrl}${path}`, {
        headers: { authorization: `Bearer ${token}`, accept: 'application/json' },
      })
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
      throw new ApiError(response.status, 'INVALID_RESPONSE', 'API から想定外の応答が返されました。')
    }
    return body as T
  }

  return {
    getMe: () => request<Me>('/api/me'),
    listCatalog: (type, nextToken) => {
      const query = new URLSearchParams({ type })
      if (nextToken !== undefined) query.set('nextToken', nextToken)
      return request<CatalogPage>(`/api/catalog?${query.toString()}`)
    },
  }
}
