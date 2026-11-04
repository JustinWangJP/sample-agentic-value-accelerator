// 起動処理が各種エラーを画面用の状態に変換することを、ブラウザ環境をフェイクにして検証する（Seam 2）
import { describe, expect, it } from 'vitest'
import type { AuthEnvironment } from '../auth/session'
import { startup, UNEXPECTED_STARTUP_ERROR } from '../startup'

const ENV = {
  VITE_COGNITO_DOMAIN: 'https://portal.auth.us-east-1.amazoncognito.com',
  VITE_COGNITO_CLIENT_ID: 'human-spa-client',
  VITE_REDIRECT_URI: 'https://portal.example.com/',
  VITE_API_BASE_URL: 'https://api.example.com',
}

const PENDING_KEY = 'registry-portal.pending-login'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

/** 認可コードのコールバックで起動し、トークン交換 → /api/me の順に応答するブラウザ環境 */
function callbackBrowser(meResponse: Response | Error): AuthEnvironment {
  const items = new Map<string, string>([
    [PENDING_KEY, JSON.stringify({ state: 's-1', verifier: 'v-1' })],
  ])
  const responses: (Response | Error)[] = [
    jsonResponse(200, { access_token: 'access-token-value', expires_in: 3600 }),
    meResponse,
  ]
  return {
    location: { href: 'https://portal.example.com/?code=auth-code&state=s-1', assign: () => {} },
    history: { replaceState: () => {} },
    sessionStorage: {
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => {
        items.set(key, value)
      },
      removeItem: (key: string) => {
        items.delete(key)
      },
    },
    fetch: async () => {
      const response = responses.shift()
      if (response === undefined) throw new Error('想定外の fetch 呼び出し')
      if (response instanceof Error) throw response
      return response
    },
    crypto: globalThis.crypto,
  }
}

describe('起動処理の結果', () => {
  it('ログイン後に自分の主体を取得できれば ready になる', async () => {
    const me = {
      principal: { type: 'human', issuer: 'https://issuer.example.com', subject: 'sub-1' },
      roles: ['Viewer'],
      teams: [],
    }

    const state = await startup(ENV, callbackBrowser(jsonResponse(200, me)))

    expect(state).toEqual({ status: 'ready', me })
  })

  it('設定が不足していれば、ログインし直す案内をせずに設定エラーを表示する', async () => {
    const state = await startup({}, callbackBrowser(new Error('呼ばれない')))

    expect(state).toMatchObject({ status: 'error', canRetryLogin: false })
    expect(state.status === 'error' && state.message).toContain('認証設定が不足しています')
  })

  it('API が 401 を返したら、共通エラー DTO のメッセージとログインし直す案内を表示する', async () => {
    const state = await startup(
      ENV,
      callbackBrowser(
        jsonResponse(401, {
          error: { code: 'UNAUTHENTICATED', message: '認証が必要です。ログインし直してください。' },
        }),
      ),
    )

    expect(state).toEqual({
      status: 'error',
      message: '認証が必要です。ログインし直してください。',
      canRetryLogin: true,
    })
  })

  it('API が 403 を返したら、ログインし直す案内をせずにメッセージを表示する', async () => {
    const state = await startup(
      ENV,
      callbackBrowser(
        jsonResponse(403, {
          error: {
            code: 'FORBIDDEN',
            message: 'このクライアントのトークンはポータル API では受け付けません。',
          },
        }),
      ),
    )

    expect(state).toEqual({
      status: 'error',
      message: 'このクライアントのトークンはポータル API では受け付けません。',
      canRetryLogin: false,
    })
  })

  it('ログインに失敗したら、ログインし直す案内を表示する', async () => {
    const browser = callbackBrowser(new Error('呼ばれない'))
    browser.location.href = 'https://portal.example.com/?code=auth-code&state=forged'

    const state = await startup(ENV, browser)

    expect(state).toMatchObject({ status: 'error', canRetryLogin: true })
  })

  it('想定外のエラーでは、内部のメッセージを出さずに固定の文言を表示する', async () => {
    const browser = callbackBrowser(new Error('呼ばれない'))
    // ストレージが使えないなど、ログイン処理が分類しない例外
    browser.sessionStorage.getItem = () => {
      throw new TypeError('内部の詳細')
    }

    const state = await startup(ENV, browser)

    expect(state).toEqual({
      status: 'error',
      message: UNEXPECTED_STARTUP_ERROR,
      canRetryLogin: true,
    })
  })
})
