// 起動処理が各種エラーを画面用の状態に変換することを、ブラウザ環境をフェイクにして検証する（Seam 2）
import { describe, expect, it } from 'vitest'
import { startup, UNEXPECTED_STARTUP_ERROR, type StartupState } from '../startup'
import {
  fakeBrowser,
  jsonResponse,
  storePendingLogin,
  TEST_CONFIG,
  TEST_VITE_ENV,
} from './helpers'

const CALLBACK_URL = 'https://portal.example.com/?code=auth-code&state=s-1'

/** 認可コードのコールバックで起動し、トークン交換 → /api/me の順に応答するブラウザ環境 */
function callbackBrowser(meResponse: Response | Error) {
  const fake = fakeBrowser(CALLBACK_URL, [
    jsonResponse(200, { access_token: 'access-token-value', expires_in: 3600 }),
    meResponse,
  ])
  storePendingLogin(fake.sessionStorage)
  return fake
}

function errorOf(state: StartupState) {
  if (state.status !== 'error') throw new Error(`error を期待したが ${state.status} だった`)
  return state
}

describe('起動処理の結果', () => {
  it('ログイン後に自分の主体を取得できれば ready になる', async () => {
    const me = {
      principal: { type: 'human', issuer: 'https://issuer.example.com', subject: 'sub-1' },
      roles: ['Viewer'],
      teams: [],
    }

    const state = await startup(TEST_VITE_ENV, callbackBrowser(jsonResponse(200, me)).browser)

    expect(state).toEqual({ status: 'ready', me })
  })

  it('API が 401 を返したら、メッセージとログインし直す操作を返す', async () => {
    const fake = callbackBrowser(
      jsonResponse(401, {
        error: { code: 'UNAUTHENTICATED', message: '認証が必要です。ログインし直してください。' },
      }),
    )

    const state = errorOf(await startup(TEST_VITE_ENV, fake.browser))

    expect(state.message).toBe('認証が必要です。ログインし直してください。')
    // ログインし直すと、コールバック URL（SPA の入口）から改めて SSO を始める
    state.retryLogin?.()
    expect(fake.redirects).toEqual([TEST_CONFIG.redirectUri])
  })

  it('ログインに失敗したら、ログインし直す操作を返す', async () => {
    const fake = fakeBrowser('https://portal.example.com/?code=auth-code&state=forged')
    storePendingLogin(fake.sessionStorage)

    const state = errorOf(await startup(TEST_VITE_ENV, fake.browser))

    expect(state.retryLogin).toBeTypeOf('function')
  })

  it.each([
    [
      '403',
      jsonResponse(403, {
        error: {
          code: 'FORBIDDEN',
          message: 'このクライアントのトークンはポータル API では受け付けません。',
        },
      }),
      'このクライアントのトークンはポータル API では受け付けません。',
    ],
    [
      '500',
      jsonResponse(500, {
        error: {
          code: 'INTERNAL_ERROR',
          message: '内部エラーが発生しました。時間をおいて再度お試しください。',
        },
      }),
      '内部エラーが発生しました。時間をおいて再度お試しください。',
    ],
    [
      '通信エラー',
      new TypeError('Failed to fetch'),
      'API に接続できません。ネットワークを確認してください。',
    ],
  ])('%s では、ログインし直す操作を返さない', async (_label, meResponse, message) => {
    const state = errorOf(await startup(TEST_VITE_ENV, callbackBrowser(meResponse).browser))

    expect(state).toEqual({ status: 'error', message })
  })

  it('設定が不足していれば、ログインし直す操作を返さずに設定エラーを表示する', async () => {
    const state = errorOf(await startup({}, callbackBrowser(new Error('呼ばれない')).browser))

    expect(state.message).toContain('認証設定が不足しています')
    expect(state.retryLogin).toBeUndefined()
  })

  it('想定外のエラーでは、内部のメッセージを出さず、ログインし直す操作も返さない', async () => {
    const fake = callbackBrowser(new Error('呼ばれない'))
    // ストレージが使えないなど、ログイン処理が分類しない例外
    fake.browser.sessionStorage.getItem = () => {
      throw new TypeError('内部の詳細')
    }

    const state = await startup(TEST_VITE_ENV, fake.browser)

    expect(state).toEqual({ status: 'error', message: UNEXPECTED_STARTUP_ERROR })
  })
})
