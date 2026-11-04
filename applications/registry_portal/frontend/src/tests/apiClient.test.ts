// API クライアントの境界を、fetch をフェイクにして検証する（Seam 2）
import { describe, expect, it } from 'vitest'
import { ApiError, createApiClient, UNAUTHENTICATED_MESSAGE } from '../api/client'
import { fakeFetch, jsonResponse, TEST_CONFIG } from './helpers'

const me = {
  principal: {
    type: 'human',
    issuer: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE',
    subject: 'sub-1',
  },
  roles: ['Viewer', 'Publisher'],
  teams: ['team-payments'],
}

function clientWith(responses: (Response | Error)[], token: string | null = 'token') {
  const { fetchImpl, calls } = fakeFetch(responses)
  const client = createApiClient({
    baseUrl: TEST_CONFIG.apiBaseUrl,
    fetch: fetchImpl,
    getAccessToken: () => token,
  })
  return { client, calls }
}

describe('自分の主体の取得', () => {
  it('メモリー上のアクセストークンを Bearer で送り、主体・業務ロール・所属チームを返す', async () => {
    const { client, calls } = clientWith([jsonResponse(200, me)], 'access-token-value')

    await expect(client.getMe()).resolves.toEqual(me)

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('https://api.example.com/api/me')
    const headers = new Headers(calls[0].init?.headers)
    expect(headers.get('authorization')).toBe('Bearer access-token-value')
    // 主体はトークンだけから決まる。メールアドレスのヘッダーは送らない
    expect(headers.get('x-user-email')).toBeNull()
  })

  it('共通エラー DTO の日本語メッセージとコードを ApiError として返す', async () => {
    const { client } = clientWith([
      jsonResponse(401, {
        error: {
          code: 'UNAUTHENTICATED',
          message: 'アクセストークンを指定してください。',
          requestId: 'req-1',
        },
      }),
    ])

    const error = await client.getMe().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 401,
      code: 'UNAUTHENTICATED',
      message: 'アクセストークンを指定してください。',
      requestId: 'req-1',
      requiresLogin: true,
    })
  })

  it('403 の共通エラー DTO は、ログインし直しても解決しないエラーとして返す', async () => {
    const { client } = clientWith([
      jsonResponse(403, {
        error: { code: 'FORBIDDEN', message: 'この操作を行う権限がありません。', requestId: null },
      }),
    ])

    await expect(client.getMe()).rejects.toMatchObject({
      status: 403,
      code: 'FORBIDDEN',
      requiresLogin: false,
    })
  })

  it('JWT authorizer が拒否した 401（共通 DTO ではない）も、ログインし直すエラーにする', async () => {
    // API Gateway の HTTP API は、トークンなし・期限切れを Lambda に届けずにこの形で返す
    const { client } = clientWith([jsonResponse(401, { message: 'Unauthorized' })])

    await expect(client.getMe()).rejects.toMatchObject({
      status: 401,
      code: 'UNAUTHENTICATED',
      message: UNAUTHENTICATED_MESSAGE,
      requiresLogin: true,
    })
  })

  it('共通 DTO ではない 401 以外のエラーは、HTTP ステータスを示す汎用のエラーにする', async () => {
    const { client } = clientWith([jsonResponse(503, { message: 'Service Unavailable' })])

    await expect(client.getMe()).rejects.toMatchObject({
      status: 503,
      code: 'UNEXPECTED_ERROR',
      message: 'API の呼び出しに失敗しました（HTTP 503）。',
      requiresLogin: false,
    })
  })

  it('JSON でない応答（HTML など）を成功として扱わない', async () => {
    const { client } = clientWith([
      new Response('<!doctype html><html></html>', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    ])

    await expect(client.getMe()).rejects.toMatchObject({
      code: 'INVALID_RESPONSE',
      message: 'API から想定外の応答が返されました。',
    })
  })

  it('通信に失敗したら日本語のエラーにする', async () => {
    const { client } = clientWith([new TypeError('Failed to fetch')])

    await expect(client.getMe()).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      message: 'API に接続できません。ネットワークを確認してください。',
      requiresLogin: false,
    })
  })

  it('アクセストークンがなければ API を呼ばずに認証エラーにする', async () => {
    const { client, calls } = clientWith([jsonResponse(200, me)], null)

    await expect(client.getMe()).rejects.toMatchObject({ status: 401, code: 'UNAUTHENTICATED' })
    expect(calls).toEqual([])
  })
})
