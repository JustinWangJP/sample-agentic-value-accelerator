// API クライアントの境界を、fetch をフェイクにして検証する（Seam 2）
import { describe, expect, it } from 'vitest'
import { ApiError, createApiClient } from '../api/client'

type FetchCall = { url: string; init?: RequestInit }

function fakeFetch(response: Response | Error) {
  const calls: FetchCall[] = []
  const fetchImpl: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), init })
    if (response instanceof Error) throw response
    return response
  }
  return { fetchImpl, calls }
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const me = {
  principal: {
    type: 'human',
    issuer: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE',
    subject: 'sub-1',
  },
  roles: ['Viewer', 'Publisher'],
  teams: ['team-payments'],
}

describe('自分の主体の取得', () => {
  it('メモリー上のアクセストークンを Bearer で送り、主体・ロール・チームを返す', async () => {
    const { fetchImpl, calls } = fakeFetch(jsonResponse(200, me))
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      fetch: fetchImpl,
      getAccessToken: () => 'access-token-value',
    })

    await expect(client.getMe()).resolves.toEqual(me)

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('https://api.example.com/api/me')
    const headers = new Headers(calls[0].init?.headers)
    expect(headers.get('authorization')).toBe('Bearer access-token-value')
    // 主体はトークンだけから決まる。メールアドレスのヘッダーは送らない
    expect(headers.get('x-user-email')).toBeNull()
  })

  it('共通エラー DTO の日本語メッセージとコードを ApiError として返す', async () => {
    const { fetchImpl } = fakeFetch(
      jsonResponse(401, {
        error: {
          code: 'UNAUTHENTICATED',
          message: 'このクライアントのトークンは受け付けません。',
          requestId: 'req-1',
        },
      }),
    )
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      fetch: fetchImpl,
      getAccessToken: () => 'token',
    })

    const error = await client.getMe().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 401,
      code: 'UNAUTHENTICATED',
      message: 'このクライアントのトークンは受け付けません。',
      requestId: 'req-1',
    })
  })

  it('JSON でない応答（HTML など）を成功として扱わない', async () => {
    const { fetchImpl } = fakeFetch(
      new Response('<!doctype html><html></html>', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    )
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      fetch: fetchImpl,
      getAccessToken: () => 'token',
    })

    await expect(client.getMe()).rejects.toMatchObject({
      code: 'INVALID_RESPONSE',
      message: 'API から想定外の応答が返されました。',
    })
  })

  it('通信に失敗したら日本語のエラーにする', async () => {
    const { fetchImpl } = fakeFetch(new TypeError('Failed to fetch'))
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      fetch: fetchImpl,
      getAccessToken: () => 'token',
    })

    await expect(client.getMe()).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      message: 'API に接続できません。ネットワークを確認してください。',
    })
  })

  it('アクセストークンがなければ API を呼ばずに認証エラーにする', async () => {
    const { fetchImpl, calls } = fakeFetch(jsonResponse(200, me))
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      fetch: fetchImpl,
      getAccessToken: () => null,
    })

    await expect(client.getMe()).rejects.toMatchObject({ status: 401, code: 'UNAUTHENTICATED' })
    expect(calls).toEqual([])
  })
})
