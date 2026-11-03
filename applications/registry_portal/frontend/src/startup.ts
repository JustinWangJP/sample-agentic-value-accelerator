// 起動処理: 設定の読み込み → ログイン（PKCE）→ 自分の主体の取得
//
// React の外で1回だけ実行する（StrictMode の二重実行でコールバック処理が重複しないようにするため）
import { ApiError, createApiClient, type Me } from './api/client'
import { createAuthSession, LoginError, type AuthEnvironment } from './auth/session'
import { ConfigError, loadConfig } from './config'

export type StartupState =
  | { status: 'redirecting' }
  | { status: 'ready'; me: Me }
  | { status: 'error'; message: string }

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
    if (error instanceof ConfigError || error instanceof LoginError || error instanceof ApiError) {
      return { status: 'error', message: error.message }
    }
    return { status: 'error', message: '起動中に想定外のエラーが発生しました。' }
  }
}
