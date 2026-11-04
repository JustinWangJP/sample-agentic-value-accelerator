// ログインセッション（Authorization Code + PKCE）
//
// - アクセストークンはこのモジュールのメモリー上だけで保持し、ブラウザのストレージには保存しない
// - ページを再読み込みするとトークンは失われ、SSO（Cognito）へリダイレクトし直す
// - sessionStorage に置くのは、リダイレクトをまたぐ間だけ必要な state と code_verifier のみ
import type { PortalConfig } from '../config'
import { codeChallengeS256, randomString } from './pkce'

export type AuthEnvironment = {
  location: { href: string; assign: (url: string) => void }
  history: { replaceState: (data: unknown, unused: string, url?: string | URL | null) => void }
  sessionStorage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
  fetch: typeof fetch
  crypto: Crypto
}

export type StartResult = { status: 'redirecting' } | { status: 'authenticated' }

type PendingLogin = { state: string; verifier: string }

/** ログイン処理の失敗。画面にそのまま表示できる日本語メッセージを持つ */
export class LoginError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'LoginError'
  }
}

const CALLBACK_PARAMS = ['code', 'state', 'error', 'error_description', 'error_uri']

/** リダイレクトをまたいで state と code_verifier を保持する sessionStorage のキー */
export const PENDING_LOGIN_KEY = 'registry-portal.pending-login'

export type AuthSession = {
  /** 起動時に呼ぶ。コールバックならトークンを取得し、未ログインなら SSO へリダイレクトする */
  start: () => Promise<StartResult>
  /** メモリー上のアクセストークン。未ログインなら null */
  getAccessToken: () => string | null
}

export function createAuthSession(config: PortalConfig, browser: AuthEnvironment): AuthSession {
  let accessToken: string | null = null

  async function redirectToLogin(): Promise<StartResult> {
    const pending: PendingLogin = {
      state: randomString(browser.crypto),
      verifier: randomString(browser.crypto, 48),
    }
    browser.sessionStorage.setItem(PENDING_LOGIN_KEY, JSON.stringify(pending))
    const url = new URL('/oauth2/authorize', config.cognitoDomain)
    url.searchParams.set('response_type', 'code')
    url.searchParams.set('client_id', config.clientId)
    url.searchParams.set('redirect_uri', config.redirectUri)
    url.searchParams.set('scope', config.scopes)
    url.searchParams.set('state', pending.state)
    url.searchParams.set('code_challenge', await codeChallengeS256(browser.crypto, pending.verifier))
    url.searchParams.set('code_challenge_method', 'S256')
    browser.location.assign(url.toString())
    return { status: 'redirecting' }
  }

  function takePendingLogin(): PendingLogin | null {
    const raw = browser.sessionStorage.getItem(PENDING_LOGIN_KEY)
    browser.sessionStorage.removeItem(PENDING_LOGIN_KEY)
    if (!raw) return null
    try {
      const value: unknown = JSON.parse(raw)
      if (
        typeof value === 'object' &&
        value !== null &&
        typeof (value as PendingLogin).state === 'string' &&
        typeof (value as PendingLogin).verifier === 'string'
      ) {
        return value as PendingLogin
      }
    } catch {
      // 壊れた値は無視する
    }
    return null
  }

  /** 認可コードなどのパラメーターを URL から取り除き、履歴に残さない */
  function clearCallbackParams(url: URL): void {
    for (const name of CALLBACK_PARAMS) url.searchParams.delete(name)
    browser.history.replaceState(null, '', url.toString())
  }

  async function exchangeCode(code: string, verifier: string): Promise<string> {
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectUri,
      code_verifier: verifier,
    })
    let response: Response
    try {
      response = await browser.fetch(new URL('/oauth2/token', config.cognitoDomain).toString(), {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      })
    } catch {
      throw new LoginError('ログインに失敗しました。認証サーバーに接続できません。')
    }
    if (!response.ok) {
      throw new LoginError('ログインに失敗しました。もう一度ログインしてください。')
    }
    const tokens: unknown = await response.json().catch(() => null)
    const token = (tokens as { access_token?: unknown } | null)?.access_token
    if (typeof token !== 'string' || token === '') {
      throw new LoginError('ログインに失敗しました。アクセストークンを取得できません。')
    }
    // ID トークンとリフレッシュトークンは使わず、どこにも保存しない
    return token
  }

  async function handleCallback(url: URL): Promise<StartResult> {
    const pending = takePendingLogin()
    const returnedState = url.searchParams.get('state')
    const code = url.searchParams.get('code')
    const error = url.searchParams.get('error')
    clearCallbackParams(url)

    if (error) {
      throw new LoginError('ログインに失敗しました。管理者に権限を確認してください。')
    }
    if (!pending || !returnedState || pending.state !== returnedState || !code) {
      throw new LoginError('ログイン要求を確認できませんでした。もう一度ログインしてください。')
    }
    accessToken = await exchangeCode(code, pending.verifier)
    return { status: 'authenticated' }
  }

  return {
    start: async () => {
      const url = new URL(browser.location.href)
      if (url.searchParams.has('code') || url.searchParams.has('error')) {
        return handleCallback(url)
      }
      return redirectToLogin()
    },
    getAccessToken: () => accessToken,
  }
}
