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

/** 応答の形を検査する型ガード */
export type ResponseGuard<T> = (value: unknown) => value is T

/**
 * 応答を型ガードで検査し、形が違えば invalidResponse の例外を投げる関数を作る。
 * 各 API モジュール（drafts/api.ts・reviews/api.ts・recovery/api.ts）が共有する
 */
export function createResponseChecker(invalidResponse: () => Error) {
  return async function checked<T>(response: Promise<unknown>, guard: ResponseGuard<T>): Promise<T> {
    const value = await response
    if (!guard(value)) throw invalidResponse()
    return value
  }
}
