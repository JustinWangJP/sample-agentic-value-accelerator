// 起動処理: 設定の読み込み → ログイン（PKCE）→ 自分の主体の取得
//
// React の外で1回だけ実行する（StrictMode の二重実行でコールバック処理が重複しないようにするため）
import { ApiError, createApiClient, type ApiClient, type Me } from './api/client'
import { createAuthSession, LoginError, type AuthEnvironment } from './auth/session'
import { ConfigError, loadConfig, type PortalConfig } from './config'

export type StartupState =
  | { status: 'redirecting' }
  /** api: ログインしたセッションのアクセストークンを使う。relogin: SSO からログインし直す */
  | { status: 'ready'; me: Me; api: ApiClient; relogin: () => void }
  /**
   * retryLogin: ログインし直せば解決しうるとき（401 とログイン失敗）だけ持つ。
   * 403・設定エラー・通信エラー・想定外のエラーでは持たない（設計書「401 と 403 の使い分け」）
   */
  | { status: 'error'; message: string; retryLogin?: () => void }

export const UNEXPECTED_STARTUP_ERROR = '起動中に想定外のエラーが発生しました。'

/** SSO からログインし直す（コールバック URL へ移動して、もう一度 PKCE を始める） */
const loginAgain = (browser: AuthEnvironment, config: PortalConfig) => () =>
  browser.location.assign(config.redirectUri)

function toErrorState(
  error: unknown,
  browser: AuthEnvironment,
  config: PortalConfig | null,
): StartupState {
  const retryLogin = config ? loginAgain(browser, config) : undefined
  if (error instanceof ApiError) {
    return error.requiresLogin
      ? { status: 'error', message: error.message, retryLogin }
      : { status: 'error', message: error.message }
  }
  if (error instanceof LoginError) {
    return { status: 'error', message: error.message, retryLogin }
  }
  if (error instanceof ConfigError) {
    return { status: 'error', message: error.message }
  }
  return { status: 'error', message: UNEXPECTED_STARTUP_ERROR }
}

/**
 * @param viteEnv ビルド時の環境変数（import.meta.env）
 * @param browser ブラウザの機能（location・history・sessionStorage・fetch・crypto）
 */
export async function startup(
  viteEnv: Record<string, string | undefined>,
  browser: AuthEnvironment,
): Promise<StartupState> {
  let config: PortalConfig | null = null
  try {
    config = loadConfig(viteEnv)
    const session = createAuthSession(config, browser)
    const result = await session.start()
    if (result.status === 'redirecting') return result
    const api = createApiClient({
      baseUrl: config.apiBaseUrl,
      fetch: browser.fetch,
      getAccessToken: session.getAccessToken,
    })
    return { status: 'ready', me: await api.getMe(), api, relogin: loginAgain(browser, config) }
  } catch (error) {
    return toErrorState(error, browser, config)
  }
}
