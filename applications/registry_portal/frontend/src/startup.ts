// 起動処理: 設定の読み込み → ログイン（PKCE）→ 自分の主体の取得
//
// React の外で1回だけ実行する（StrictMode の二重実行でコールバック処理が重複しないようにするため）
import { ApiError, createApiClient, type Me } from './api/client'
import { createAuthSession, LoginError, type AuthEnvironment } from './auth/session'
import { ConfigError, loadConfig } from './config'

export type StartupState =
  | { status: 'redirecting' }
  | { status: 'ready'; me: Me }
  /** canRetryLogin: ログインし直せば解決しうるか（403 など権限の問題では false） */
  | { status: 'error'; message: string; canRetryLogin: boolean }

export const UNEXPECTED_STARTUP_ERROR = '起動中に想定外のエラーが発生しました。'

function toErrorState(error: unknown): StartupState {
  if (error instanceof ApiError) {
    // 403 は正規のトークンでも許可されない呼び出し。ログインし直しても解決しない
    return { status: 'error', message: error.message, canRetryLogin: error.status !== 403 }
  }
  if (error instanceof ConfigError) {
    // 設定の不足はビルド時の問題なので、ログインし直しても解決しない
    return { status: 'error', message: error.message, canRetryLogin: false }
  }
  if (error instanceof LoginError) {
    return { status: 'error', message: error.message, canRetryLogin: true }
  }
  return { status: 'error', message: UNEXPECTED_STARTUP_ERROR, canRetryLogin: true }
}

export async function startup(
  env: Record<string, string | undefined>,
  browser: AuthEnvironment,
): Promise<StartupState> {
  try {
    const config = loadConfig(env)
    const session = createAuthSession(config, browser)
    const result = await session.start()
    if (result.status === 'redirecting') return result
    const api = createApiClient({
      baseUrl: config.apiBaseUrl,
      fetch: browser.fetch,
      getAccessToken: session.getAccessToken,
    })
    return { status: 'ready', me: await api.getMe() }
  } catch (error) {
    return toErrorState(error)
  }
}
