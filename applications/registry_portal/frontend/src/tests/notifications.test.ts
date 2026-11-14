// @vitest-environment happy-dom
// 画面上の通知（Seam 2: SPA の fetch 境界）。ヘッダーの未読件数、約60秒ごとのポーリング、
// 一覧からの既読、0件と取得失敗の区別、応答の検査を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { NOTIFICATION_POLL_INTERVAL_MS } from '../notifications/api'
import { NotificationsView } from '../notifications/NotificationsView'
import { fakeFetch, jsonResponse } from './helpers'

const NEWER = '2026-11-14T01:00:00.000000Z#' + 'b'.repeat(64)
const OLDER = '2026-11-14T00:00:00.000000Z#' + 'a'.repeat(64)
const notification = (position: string, overrides: Record<string, unknown> = {}) => ({
  notificationId: position.split('#')[1],
  position,
  kind: 'SUBMITTED',
  recordId: 'agentABC1234',
  recordName: '決済エージェント',
  message: '「決済エージェント」の公開申請が届きました。審査待ちから確認してください。',
  createdAt: position.split('#')[0],
  read: false,
  ...overrides,
})
const inbox = (unreadCount: number, items = [notification(NEWER), notification(OLDER)], readUpTo: string | null = null) =>
  jsonResponse(200, { items, unreadCount, readUpTo })

let container: HTMLDivElement
let root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function button(label: string): HTMLButtonElement {
  const node = [...container.querySelectorAll('button')].find((element) =>
    element.textContent?.startsWith(label),
  )
  if (!node) throw new Error(`ボタンがありません ${label}`)
  return node
}
async function click(label: string) {
  await act(() => button(label).click())
}
async function mount(responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  const onRelogin = vi.fn()
  await act(() => root.render(createElement(NotificationsView, { api, onRelogin })))
  return { calls, onRelogin }
}
async function poll() {
  await act(async () => {
    vi.advanceTimersByTime(NOTIFICATION_POLL_INTERVAL_MS)
  })
}

it('表示すると通知を取得し、ヘッダーに未読件数を出す。一覧は開くまで出さない', async () => {
  const { calls } = await mount([inbox(2)])
  expect(calls.map((call) => call.url)).toEqual(['/api/notifications'])
  expect(button('通知').textContent).toBe('通知（未読 2 件）')
  expect(container.textContent).not.toContain('公開申請が届きました')

  await click('通知')
  const items = [...container.querySelectorAll('li')]
  expect(items).toHaveLength(2)
  expect(items[0]?.textContent).toContain('公開申請')
  expect(items[0]?.textContent).toContain('決済エージェント')
  expect(items[0]?.textContent).toContain('未読')
})

it('約60秒ごとにポーリングして未読件数を更新し、表示をやめたら止める', async () => {
  const { calls } = await mount([inbox(0, []), inbox(1, [notification(NEWER)])])
  expect(button('通知').textContent).toBe('通知（未読 0 件）')
  await poll()
  expect(calls).toHaveLength(2)
  expect(button('通知').textContent).toBe('通知（未読 1 件）')

  await act(() => root.unmount())
  root = createRoot(container)
  vi.advanceTimersByTime(NOTIFICATION_POLL_INTERVAL_MS * 3)
  expect(calls).toHaveLength(2)
})

it('一覧から既読にすると、最新の通知の位置を送り、未読件数を更新する', async () => {
  const read = [notification(NEWER, { read: true }), notification(OLDER, { read: true })]
  const { calls } = await mount([inbox(2), inbox(0, read, NEWER)])
  await click('通知')
  await click('すべて既読にする')
  expect(calls[1]?.url).toBe('/api/notifications/read')
  expect(calls[1]?.init?.method).toBe('POST')
  expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({ readUpTo: NEWER })
  expect(button('通知').textContent).toBe('通知（未読 0 件）')
  expect([...container.querySelectorAll('li')].some((item) => item.textContent?.includes('未読'))).toBe(false)
  // 未読がなければ既読にするボタンを出さない
  expect(() => button('すべて既読にする')).toThrow()
})

it('0件と取得の失敗を区別して表示し、401 ではログインし直しを案内する', async () => {
  await mount([inbox(0, [])])
  await click('通知')
  expect(container.textContent).toContain('通知はありません。')
  await act(() => root.unmount())

  root = createRoot(container)
  const error = { error: { code: 'NOTIFICATIONS_UNAVAILABLE', message: '通知を取得できませんでした。時間をおいて再度お試しください。' } }
  await mount([jsonResponse(502, error), inbox(0, [])])
  expect(button('通知').textContent).toBe('通知（未読件数を取得できません）')
  await click('通知')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('通知を取得できませんでした')
  expect(container.textContent).not.toContain('通知はありません。')
  await click('再試行')
  expect(container.textContent).toContain('通知はありません。')
  await act(() => root.unmount())

  root = createRoot(container)
  const { onRelogin } = await mount([jsonResponse(401, { message: 'Unauthorized' })])
  await click('通知')
  await click('もう一度ログインする')
  expect(onRelogin).toHaveBeenCalled()
})

it('想定外の種別や必須の項目を欠いた応答は、通知として表示しない', async () => {
  for (const item of [notification(NEWER, { kind: 'UNKNOWN' }), notification(NEWER, { position: undefined })]) {
    await mount([inbox(1, [item])])
    await click('通知')
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('想定外の応答')
    await act(() => root.unmount())
    root = createRoot(container)
  }
  await mount([jsonResponse(200, { items: [], unreadCount: '1', readUpTo: null })])
  await click('通知')
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
})

it('通知の準備中（503）は、サーバーの案内を表示し、0件とは区別する', async () => {
  const notConfigured = {
    error: { code: 'NOTIFICATIONS_NOT_CONFIGURED', message: '通知は準備中です。管理者に連絡してください。' },
  }
  await mount([jsonResponse(503, notConfigured)])
  expect(button('通知').textContent).toBe('通知（未読件数を取得できません）')
  await click('通知')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('通知は準備中です')
  expect(container.textContent).not.toContain('通知はありません。')
})
