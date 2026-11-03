// PKCE（RFC 7636）の code_verifier と code_challenge（S256）を作る

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** 推測できない乱数文字列（base64url）を作る */
export function randomString(crypto: Crypto, byteLength = 32): string {
  return base64UrlEncode(crypto.getRandomValues(new Uint8Array(byteLength)))
}

/** code_verifier から S256 の code_challenge を計算する */
export async function codeChallengeS256(crypto: Crypto, verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64UrlEncode(new Uint8Array(digest))
}
