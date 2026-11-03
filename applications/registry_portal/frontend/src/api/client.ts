// Registry ポータル API のクライアント
//
// - アクセストークン（ID トークンではない）を Bearer で送る
// - エラーは共通エラー DTO の日本語メッセージを使い、ApiError として返す
// - JSON でない応答（SPA の index.html など）を成功として扱わない

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
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  async function request<T>(path: string): Promise<T> {
    const token = options.getAccessToken()
    if (!token) {
      throw new ApiError(401, 'UNAUTHENTICATED', '認証が必要です。ログインし直してください。')
    }
    let response: Response
    try {
      response = await options.fetch(`${options.baseUrl.replace(/\/+$/, '')}${path}`, {
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
  }
}
