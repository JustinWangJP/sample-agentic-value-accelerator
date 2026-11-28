// レコードの状態と公開停止中の表示。公開管理のパネルと管理レコードの検索一覧で、同じ文言と色を使う
import { DEPRECATED_STATUS, PUBLISHED_STATUS, recordStatusLabel, type RecordStatus } from '../drafts/api'
import { badge } from '../ui/classes'
import type { PublicationState } from './api'

/** 公開停止中の表示。検索条件の選択肢と、レコードの状態のバッジで同じ文言を使う */
export const SUSPENDED_LABEL = '公開停止中'

type RecordPublicationStatus = { status: RecordStatus } & Pick<PublicationState, 'suspended'>

/**
 * 公開停止中として表示するか。API は廃止済みを公開停止中に含めない契約（PublicationState.suspended）だが、
 * 廃止は元に戻せず公開停止より優先して見せるため、表示の側でも廃止済みを除く
 */
export function isShownAsSuspended({ status, suspended }: RecordPublicationStatus): boolean {
  return suspended && status !== DEPRECATED_STATUS
}

/** レコードの状態のバッジの文言と色。公開中は緑、公開停止中は橙、廃止とそれ以外は中立 */
export function recordStatusBadge(record: RecordPublicationStatus): { label: string; className: string } {
  const suspended = isShownAsSuspended(record)
  const label = `${recordStatusLabel(record.status)}${suspended ? `・${SUSPENDED_LABEL}` : ''}`
  if (suspended) return { label, className: badge.suspended }
  if (record.status === PUBLISHED_STATUS) return { label, className: badge.success }
  return { label, className: badge.neutral }
}
