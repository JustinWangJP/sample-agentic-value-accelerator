/**
 * 本番ビルドの index.html に meta で埋め込む CSP（connect-src だけ）を作る。
 *
 * CloudFront のレスポンスヘッダーの CSP は、HTTP API の CORS が CloudFront の origin を参照するため
 * 循環参照になり、execute-api をリージョン単位（https://*.execute-api.<region>.amazonaws.com）でしか指定できない。
 * ブラウザは複数の CSP をすべて満たす通信だけを許可するので、この meta で接続先を自分の HTTP API と Cognito に絞る。
 * 接続先が両方とも未設定のビルド（CI の検査）では埋め込まない。片方だけの設定は誤りとしてビルドを止める。
 */
export function connectSrcPolicy(env: Record<string, string | undefined>): string | null {
  const apiBaseUrl = (env.VITE_API_BASE_URL ?? '').trim()
  const cognitoDomain = (env.VITE_COGNITO_DOMAIN ?? '').trim()
  if (apiBaseUrl === '' && cognitoDomain === '') {
    return null
  }
  if (apiBaseUrl === '') {
    throw new Error('connect-src の CSP を作れません: VITE_API_BASE_URL が未設定です')
  }
  if (cognitoDomain === '') {
    throw new Error('connect-src の CSP を作れません: VITE_COGNITO_DOMAIN が未設定です')
  }
  return `connect-src 'self' ${new URL(apiBaseUrl).origin} ${new URL(cognitoDomain).origin}`
}
