export type HttpMethod = 'GET' | 'POST' | 'PATCH'

/**
 * 認証ヘッダーを付けてポータル API を呼ぶ関数。GET は本文なし、それ以外は JSON の本文を送る。
 * 応答の形は呼び出し側で検証する
 */
export type ApiRequest = <T>(
  method: HttpMethod,
  path: string,
  payload?: unknown,
  extraHeaders?: Record<string, string>,
) => Promise<T>
