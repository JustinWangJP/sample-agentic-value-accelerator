// テスト共通のフェイクと fixture（Seam 2 で使う）
import type { AuthEnvironment } from '../auth/session'
import { PENDING_LOGIN_KEY } from '../auth/session'
import type { PortalConfig } from '../config'

/** loadConfig が返す形の設定（末尾の / は取り除き済み） */
export const TEST_CONFIG: PortalConfig = {
  cognitoDomain: 'https://portal.auth.us-east-1.amazoncognito.com',
  clientId: 'human-spa-client',
  redirectUri: 'https://portal.example.com/',
  apiBaseUrl: 'https://api.example.com',
  scopes: 'openid',
}

/** TEST_CONFIG に対応するビルド時の環境変数（import.meta.env） */
export const TEST_VITE_ENV: Record<string, string | undefined> = {
  VITE_COGNITO_DOMAIN: TEST_CONFIG.cognitoDomain,
  VITE_COGNITO_CLIENT_ID: TEST_CONFIG.clientId,
  VITE_REDIRECT_URI: TEST_CONFIG.redirectUri,
  VITE_API_BASE_URL: TEST_CONFIG.apiBaseUrl,
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

export type FetchCall = { url: string; init?: RequestInit }

/** 呼ばれた順に responses を返す fetch。Error を渡すと、その呼び出しで throw する */
export function fakeFetch(responses: (Response | Error)[]) {
  const calls: FetchCall[] = []
  const queue = [...responses]
  const fetchImpl: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), init })
    const response = queue.shift()
    if (response === undefined) throw new Error('想定外の fetch 呼び出し')
    if (response instanceof Error) throw response
    return response
  }
  return { fetchImpl, calls }
}

export class MemoryStorage {
  readonly items = new Map<string, string>()
  getItem(key: string): string | null {
    return this.items.get(key) ?? null
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value)
  }
  removeItem(key: string): void {
    this.items.delete(key)
  }
}

/** ログイン前に保存しておく state と code_verifier を、sessionStorage に置く */
export function storePendingLogin(storage: MemoryStorage, state = 's-1', verifier = 'v-1'): void {
  storage.setItem(PENDING_LOGIN_KEY, JSON.stringify({ state, verifier }))
}

/** href のページを開いたブラウザ。fetch は responses を順に返す */
export function fakeBrowser(href: string, responses: (Response | Error)[] = []) {
  const redirects: string[] = []
  const replacedUrls: string[] = []
  const sessionStorage = new MemoryStorage()
  const { fetchImpl, calls } = fakeFetch(responses)
  const browser: AuthEnvironment = {
    location: {
      href,
      assign: (url: string) => {
        redirects.push(url)
      },
    },
    history: {
      replaceState: (_data: unknown, _unused: string, url?: string | URL | null) => {
        replacedUrls.push(String(url))
      },
    },
    sessionStorage,
    fetch: fetchImpl,
    crypto: globalThis.crypto,
  }
  return { browser, calls, redirects, replacedUrls, sessionStorage }
}
