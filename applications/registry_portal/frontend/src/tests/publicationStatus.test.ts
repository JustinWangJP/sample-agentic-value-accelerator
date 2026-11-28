// レコードの状態のバッジ（公開管理のパネルと管理レコードの検索一覧で共有する表示）
import { describe, expect, it } from 'vitest'
import { recordStatusBadge, isShownAsSuspended } from '../publication/publicationStatus'
import { badge } from '../ui/classes'

describe('レコードの状態のバッジ', () => {
  it('公開中（APPROVED）は緑、公開停止中は橙で、公開停止中は AWS の状態の後ろに区別を添える', () => {
    expect(recordStatusBadge({ status: 'APPROVED', suspended: false })).toEqual({
      label: 'APPROVED',
      className: badge.success,
    })
    expect(recordStatusBadge({ status: 'REJECTED', suspended: true })).toEqual({
      label: 'REJECTED・公開停止中',
      className: badge.suspended,
    })
  })

  it('廃止済みは公開停止の印があっても公開停止中と表示しない（パネルと検索一覧で同じ表示にする）', () => {
    const record = { status: 'DEPRECATED', suspended: true } as const
    expect(isShownAsSuspended(record)).toBe(false)
    expect(recordStatusBadge(record)).toEqual({ label: 'DEPRECATED', className: badge.neutral })
  })

  it('それ以外の状態は中立の色で表示する', () => {
    expect(recordStatusBadge({ status: 'PENDING_APPROVAL', suspended: false }).className).toBe(badge.neutral)
  })
})
