import { describe, expect, it } from 'vitest'
import { connectSrcPolicy } from '../connectCsp'

describe('ビルド時に埋め込む connect-src の CSP', () => {
  it('接続先を自分の HTTP API と Cognito の origin だけに絞る', () => {
    expect(
      connectSrcPolicy({
        VITE_API_BASE_URL: 'https://abcde12345.execute-api.us-east-1.amazonaws.com/',
        VITE_COGNITO_DOMAIN: 'https://portal.auth.us-east-1.amazoncognito.com',
      }),
    ).toBe(
      "connect-src 'self' https://abcde12345.execute-api.us-east-1.amazonaws.com https://portal.auth.us-east-1.amazoncognito.com",
    )
  })

  it('接続先が未設定のビルド（CI の検査）では埋め込まない', () => {
    expect(connectSrcPolicy({})).toBeNull()
    expect(connectSrcPolicy({ VITE_API_BASE_URL: ' ', VITE_COGNITO_DOMAIN: '' })).toBeNull()
  })

  it('片方だけ設定されたビルドは、制限が外れないよう失敗させる', () => {
    expect(() =>
      connectSrcPolicy({ VITE_API_BASE_URL: 'https://abcde12345.execute-api.us-east-1.amazonaws.com' }),
    ).toThrow('VITE_COGNITO_DOMAIN')
    expect(() => connectSrcPolicy({ VITE_COGNITO_DOMAIN: 'https://portal.auth.us-east-1.amazoncognito.com' })).toThrow(
      'VITE_API_BASE_URL',
    )
  })

  it('URL として解釈できない値はビルドを失敗させる', () => {
    expect(() =>
      connectSrcPolicy({ VITE_API_BASE_URL: 'not a url', VITE_COGNITO_DOMAIN: 'https://portal.auth.us-east-1.amazoncognito.com' }),
    ).toThrow()
  })
})
