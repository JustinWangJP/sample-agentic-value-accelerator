// API の失敗を、画面で区別する状態に分類する（CODING_STANDARDS.md「0件、未取得、準備中、失敗を利用者が区別できるか」）。
// UI プロトタイプ README「4 状態の厳密な区別」: 401 は再ログインあり / 403 は再ログイン・再試行なし / 5xx は再試行あり。
// ただし準備中（503）は、準備が終わるまで再試行しても解決しないため再試行なし
import { A2A_SEARCH_NOT_READY } from '../api/client'
import { DRAFTS_NOT_CONFIGURED } from '../drafts/api'
import { NOTIFICATIONS_NOT_CONFIGURED } from '../notifications/api'

/**
 * login: ログインし直せば解決する（401）
 * preparing: 機能の準備が終わっていない（503 の準備中のコード）
 * forbidden: 業務ロールなどが足りない（403）
 * failure: それ以外の失敗。再試行で解決しうる
 */
export type RequestErrorKind = 'login' | 'preparing' | 'forbidden' | 'failure'

/** 準備中を表す 503 のエラーコード。バックエンドの A2ASearchNotReadyError・DraftsNotConfigured・NotificationsNotConfigured */
const PREPARING_CODES: ReadonlySet<string> = new Set([
  A2A_SEARCH_NOT_READY,
  DRAFTS_NOT_CONFIGURED,
  NOTIFICATIONS_NOT_CONFIGURED,
])

export function requestErrorKind(error: { requiresLogin: boolean; status?: number; code?: string }): RequestErrorKind {
  if (error.requiresLogin) return 'login'
  if (error.status === 503 && error.code !== undefined && PREPARING_CODES.has(error.code)) return 'preparing'
  if (error.status === 403) return 'forbidden'
  return 'failure'
}
