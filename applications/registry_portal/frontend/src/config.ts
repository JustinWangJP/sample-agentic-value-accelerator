// SPA の実行時設定
export type PortalConfig = {
  /** Cognito のドメイン（例: https://xxx.auth.us-east-1.amazoncognito.com） */
  cognitoDomain: string
  /** 人間用の公開 SPA client の client_id */
  clientId: string
  /** Cognito の client に登録したコールバック URL */
  redirectUri: string
  /** REST API のベース URL（ステージ名 /v1 を含む） */
  apiBaseUrl: string
  /** 要求する scope（スペース区切り） */
  scopes: string
}

/** 設定の不足や誤り。起動を止め、ログイン処理を行わない */
export class ConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConfigError'
  }
}

type Env = Record<string, string | undefined>

/**
 * 必須の設定。VITE_COGNITO_SCOPES には openid と、REST API のすべての経路が Cognito User Pools authorizer で検証する
 * カスタム scope を指定する。scope の値は Terraform の本体スタック（infrastructure/main.tf の
 * local.portal_api_scope）だけに定義し、配信時は output の frontend_config から渡す
 */
const REQUIRED_KEYS = [
  'VITE_COGNITO_DOMAIN',
  'VITE_COGNITO_CLIENT_ID',
  'VITE_REDIRECT_URI',
  'VITE_API_BASE_URL',
  'VITE_COGNITO_SCOPES',
] as const

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, '')

/**
 * ビルド時の環境変数（import.meta.env）から設定を読み込む。
 * 未設定の項目があれば ConfigError とする。開発用に認証を通す既定値は持たない。
 */
export function loadConfig(env: Env): PortalConfig {
  const readEnv = (key: string) => (env[key] ?? '').trim()
  const missing = REQUIRED_KEYS.filter((key) => readEnv(key) === '')
  if (missing.length > 0) {
    throw new ConfigError(`認証設定が不足しています: ${missing.join(', ')}`)
  }
  const cognitoDomain = trimTrailingSlash(readEnv('VITE_COGNITO_DOMAIN'))
  if (!cognitoDomain.startsWith('https://')) {
    throw new ConfigError('認証設定が正しくありません: VITE_COGNITO_DOMAIN は https で指定してください')
  }
  return {
    cognitoDomain,
    clientId: readEnv('VITE_COGNITO_CLIENT_ID'),
    redirectUri: readEnv('VITE_REDIRECT_URI'),
    apiBaseUrl: trimTrailingSlash(readEnv('VITE_API_BASE_URL')),
    scopes: readEnv('VITE_COGNITO_SCOPES'),
  }
}
