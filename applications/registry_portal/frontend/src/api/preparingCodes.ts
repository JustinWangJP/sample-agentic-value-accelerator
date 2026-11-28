// 準備中（機能の準備が終わっていない）を表す 503 のエラーコードを一か所に集める。
// 503 には準備中以外（ADMINISTRATION_UNAVAILABLE・APPROVAL_CONFIGURATION_CHANGED）もあるため、コードで区別する。
// 準備中のコードが増えたときは、このファイルだけを変更する
import { A2A_SEARCH_NOT_READY } from './client'
import { DRAFTS_NOT_CONFIGURED } from '../drafts/api'

/**
 * 補助テーブルが未設定で、通知を提供できないときのエラーコード。バックエンドの NotificationsNotConfigured と揃える。
 * 各 api.ts は変更しない方針（Issue #85）のため、notifications/api.ts ではなくここで定義する
 */
const NOTIFICATIONS_NOT_CONFIGURED = 'NOTIFICATIONS_NOT_CONFIGURED'

/** 準備中を表す 503 のエラーコード。バックエンドの A2ASearchNotReadyError・DraftsNotConfigured・NotificationsNotConfigured */
const PREPARING_CODES: ReadonlySet<string> = new Set([
  A2A_SEARCH_NOT_READY,
  DRAFTS_NOT_CONFIGURED,
  NOTIFICATIONS_NOT_CONFIGURED,
])

/** 機能の準備が終わっていないことを表す失敗か。準備が終わるまでは再試行しても解決しない */
export function isPreparingError({ status, code }: { status?: number; code?: string }): boolean {
  return status === 503 && code !== undefined && PREPARING_CODES.has(code)
}
