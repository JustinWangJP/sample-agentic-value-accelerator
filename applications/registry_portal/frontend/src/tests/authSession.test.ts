// ログイン（Authorization Code + PKCE）の振る舞いを、fetch・storage・location をフェイクにして検証する（Seam 2）
import { describe, expect, it } from 'vitest'
import { createAuthSession } from '../auth/session'
import { fakeBrowser, jsonResponse, storePendingLogin, TEST_CONFIG as config } from './helpers'

// 期待値は実装を使わず、Web Crypto で独立に計算する
async function sha256Base64Url(text: string): Promise<string> {
  const digest = new Uint8Array(
    await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)),
  )
  return btoa(String.fromCharCode(...digest))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

describe('未ログインでの起動', () => {
  it('PKCE（S256）付きで Cognito の authorize エンドポイントへリダイレクトする', async () => {
    const fake = fakeBrowser('https://portal.example.com/')

    const result = await createAuthSession(config, fake.browser).start()

    expect(result.status).toBe('redirecting')
    expect(fake.redirects).toHaveLength(1)
    const url = new URL(fake.redirects[0])
    expect(url.origin + url.pathname).toBe(
      'https://portal.auth.us-east-1.amazoncognito.com/oauth2/authorize',
    )
    expect(url.searchParams.get('response_type')).toBe('code')
    expect(url.searchParams.get('client_id')).toBe('human-spa-client')
    expect(url.searchParams.get('redirect_uri')).toBe('https://portal.example.com/')
    expect(url.searchParams.get('scope')).toBe('openid')
    expect(url.searchParams.get('code_challenge_method')).toBe('S256')
    expect(url.searchParams.get('state')).toBeTruthy()

    // 保存した verifier の SHA-256 が code_challenge と一致する
    const pending = JSON.parse(fake.sessionStorage.items.values().next().value ?? '{}')
    expect(pending.state).toBe(url.searchParams.get('state'))
    expect(url.searchParams.get('code_challenge')).toBe(
      await sha256Base64Url(pending.verifier),
    )
  })
})

describe('コールバックでの起動', () => {
  it('認可コードを code_verifier 付きで交換し、アクセストークンをメモリー上だけで保持する', async () => {
    const fake = fakeBrowser('https://portal.example.com/?code=auth-code&state=s-1', [
      jsonResponse(200, {
        access_token: 'access-token-value',
        id_token: 'id-token-value',
        refresh_token: 'refresh-token-value',
        token_type: 'Bearer',
        expires_in: 3600,
      }),
    ])
    storePendingLogin(fake.sessionStorage)
    const session = createAuthSession(config, fake.browser)

    const result = await session.start()

    expect(result.status).toBe('authenticated')
    expect(session.getAccessToken()).toBe('access-token-value')

    // トークンエンドポイントへの交換リクエスト（公開 client なので secret は送らない）
    expect(fake.calls).toHaveLength(1)
    expect(fake.calls[0].url).toBe('https://portal.auth.us-east-1.amazoncognito.com/oauth2/token')
    expect(fake.calls[0].init?.method).toBe('POST')
    const form = new URLSearchParams(String(fake.calls[0].init?.body))
    expect(Object.fromEntries(form)).toEqual({
      grant_type: 'authorization_code',
      client_id: 'human-spa-client',
      code: 'auth-code',
      redirect_uri: 'https://portal.example.com/',
      code_verifier: 'v-1',
    })
    expect(new Headers(fake.calls[0].init?.headers).get('authorization')).toBeNull()

    // ストレージにはトークンを残さず、使い終えた verifier も消す。URL からコードを消す
    expect(fake.sessionStorage.items.size).toBe(0)
    expect(fake.replacedUrls).toEqual(['https://portal.example.com/'])
    expect(fake.redirects).toEqual([])
  })

  it('再読み込みすると新しいセッションにはトークンがなく、SSO へリダイレクトする', async () => {
    const first = fakeBrowser('https://portal.example.com/?code=auth-code&state=s-1', [
      jsonResponse(200, { access_token: 'access-token-value', expires_in: 3600 }),
    ])
    storePendingLogin(first.sessionStorage)
    await createAuthSession(config, first.browser).start()

    // 同じストレージのまま、ページを読み込み直す
    const reload = fakeBrowser('https://portal.example.com/')
    for (const [key, value] of first.sessionStorage.items) reload.sessionStorage.setItem(key, value)
    const session = createAuthSession(config, reload.browser)
    const result = await session.start()

    expect(result.status).toBe('redirecting')
    expect(session.getAccessToken()).toBeNull()
    expect(reload.redirects).toHaveLength(1)
  })

  it('state が一致しなければコードを交換せずにエラーにする', async () => {
    const fake = fakeBrowser('https://portal.example.com/?code=auth-code&state=forged')
    storePendingLogin(fake.sessionStorage)

    await expect(createAuthSession(config, fake.browser).start()).rejects.toThrow(
      'ログイン要求を確認できませんでした',
    )
    expect(fake.calls).toEqual([])
    expect(fake.redirects).toEqual([])
  })

  it('Cognito がエラーを返したら、リダイレクトを繰り返さずにエラーにする', async () => {
    const fake = fakeBrowser(
      'https://portal.example.com/?error=access_denied&error_description=denied&state=s-1',
    )
    storePendingLogin(fake.sessionStorage)

    await expect(createAuthSession(config, fake.browser).start()).rejects.toThrow('ログインに失敗しました')
    expect(fake.redirects).toEqual([])
  })

  it('トークンの交換に失敗したらエラーにする', async () => {
    const fake = fakeBrowser('https://portal.example.com/?code=auth-code&state=s-1', [
      jsonResponse(400, { error: 'invalid_grant' }),
    ])
    storePendingLogin(fake.sessionStorage)
    const session = createAuthSession(config, fake.browser)

    await expect(session.start()).rejects.toThrow('ログインに失敗しました')
    expect(session.getAccessToken()).toBeNull()
  })
})
