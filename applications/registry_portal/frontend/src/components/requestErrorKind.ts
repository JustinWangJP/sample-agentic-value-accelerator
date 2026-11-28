// API の失敗を、画面で区別する状態に分類する（CODING_STANDARDS.md「0件、未取得、準備中、失敗を利用者が区別できるか」）。
// UI プロトタイプ README「4 状態の厳密な区別」: 401 は再ログインあり / 403 は再ログイン・再試行なし / 5xx は再試行あり。
// ただし準備中（503）は、準備が終わるまで再試行しても解決しないため再試行なし
import type { ApiError } from '../api/client'
import { isPreparingError } from '../api/preparingCodes'

/**
 * 画面に表示する失敗。ApiError のほか、ページ送りの失敗などメッセージだけを持つ値も受け付けるため、
 * 状態とエラーコードは省略できる
 */
export type DisplayedError = Pick<ApiError, 'message' | 'requiresLogin'> &
  Partial<Pick<ApiError, 'status' | 'code' | 'requestId'>>

/**
 * login: ログインし直せば解決する（401）
 * preparing: 機能の準備が終わっていない（503 の準備中のコード）
 * forbidden: 業務ロールなどが足りない（403）
 * failure: それ以外の失敗。再試行で解決しうる
 */
export type RequestErrorKind = 'login' | 'preparing' | 'forbidden' | 'failure'

export function requestErrorKind(error: Omit<DisplayedError, 'message'>): RequestErrorKind {
  if (error.requiresLogin) return 'login'
  if (isPreparingError(error)) return 'preparing'
  if (error.status === 403) return 'forbidden'
  return 'failure'
}
