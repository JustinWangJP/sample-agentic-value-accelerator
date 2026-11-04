// SPA の設定読み込み。未設定のときは起動エラーにし、開発用に認証を通す経路は持たない
import { describe, expect, it } from 'vitest'
import { ConfigError, loadConfig } from '../config'

const complete = {
  VITE_COGNITO_DOMAIN: 'https://portal.auth.us-east-1.amazoncognito.com/',
  VITE_COGNITO_CLIENT_ID: 'human-spa-client',
  VITE_REDIRECT_URI: 'https://portal.example.com/',
  VITE_API_BASE_URL: 'https://api.example.com/',
}

describe('設定の読み込み', () => {
  it('必須項目がそろっていれば設定を返し、scope の既定値は openid にする', () => {
    expect(loadConfig(complete)).toEqual({
      cognitoDomain: 'https://portal.auth.us-east-1.amazoncognito.com',
      clientId: 'human-spa-client',
      redirectUri: 'https://portal.example.com/',
      apiBaseUrl: 'https://api.example.com',
      scopes: 'openid',
    })
  })

  it.each(['VITE_COGNITO_DOMAIN', 'VITE_COGNITO_CLIENT_ID', 'VITE_REDIRECT_URI', 'VITE_API_BASE_URL'])(
    '%s が未設定なら ConfigError にする',
    (key) => {
      const env: Record<string, string | undefined> = { ...complete, [key]: '  ' }

      expect(() => loadConfig(env)).toThrow(ConfigError)
      expect(() => loadConfig(env)).toThrow(`認証設定が不足しています: ${key}`)
    },
  )

  it('https でない Cognito ドメインは受け付けない', () => {
    expect(() =>
      loadConfig({ ...complete, VITE_COGNITO_DOMAIN: 'http://portal.auth.example.com' }),
    ).toThrow(ConfigError)
  })
})
