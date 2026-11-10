// @vitest-environment happy-dom
// Admin の回復操作（Seam 2: SPA の fetch 境界）。結果確認中の操作の確認、Registry の再取得による照合、
// 確定できない操作の強制解除の本文と、結果確認中・成功・失敗・回復結果の表示を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { RecoveryView } from '../recovery/RecoveryView'
import { fakeFetch, jsonResponse } from './helpers'

const RECORD_ID = 'agentABC1234'
const issuer = 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE'
const target = {
  kind: 'APPROVE',
  operationState: 'VERIFYING',
  principal: { issuer, subject: 'curator-1' },
  startedAt: '2026-11-09T09:00:00+00:00',
  recoverableAt: '2026-11-09T09:05:00+00:00',
  recoverable: true,
}
const recovery = (mode: string) => ({
  mode,
  by: { issuer, subject: 'admin-1' },
  at: '2026-11-09T10:00:00+00:00',
  reason: 'Registry を確認した',
})

let container: HTMLDivElement
let root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

type Field = HTMLInputElement | HTMLTextAreaElement
function field(name: string): Field {
  const node = container.querySelector<Field>(`[name="${name}"]`)
  if (!node) throw new Error(`項目がありません ${name}`)
  return node
}
function button(label: string): HTMLButtonElement {
  const node = [...container.querySelectorAll('button')].find(
    (element) => element.textContent === label,
  )
  if (!node) throw new Error(`ボタンがありません ${label}`)
  return node
}
async function click(label: string) {
  await act(() => button(label).click())
}
async function setValue(name: string, value: string) {
  const node = field(name)
  const prototype =
    node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set
  await act(() => {
    setter?.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function mount(responses: (Response | Error)[], roles: string[] = ['Admin']) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  await act(() => root.render(createElement(RecoveryView, { api, roles, onRelogin: vi.fn() })))
  return calls
}
function sentBody(calls: { init?: RequestInit }[], index: number) {
  return JSON.parse(String(calls[index]?.init?.body))
}
async function inspect(responses: (Response | Error)[]) {
  const calls = await mount(responses)
  await setValue('recoveryRecordId', RECORD_ID)
  await click('結果確認中の操作を確認')
  return calls
}

it('Admin 以外には回復の画面を出さず、API も呼ばない', async () => {
  const calls = await mount([], ['Curator', 'Publisher'])
  expect(container.textContent).not.toContain('回復操作')
  expect(calls).toHaveLength(0)
})

it('レコードの結果確認中の操作を確認し、操作の主体と開始時刻を表示する', async () => {
  const calls = await inspect([jsonResponse(200, { recordId: RECORD_ID, operation: target })])
  expect(calls[0]?.url).toBe(`/api/records/${RECORD_ID}/recovery`)
  expect(container.textContent).toContain('公開承認・結果確認中')
  expect(container.textContent).toContain('主体 curator-1')
  expect(container.textContent).toContain('2026-11-09T09:00:00+00:00')
})

it('結果確認中の操作がないレコードは、取得の失敗と区別して表示する', async () => {
  await inspect([jsonResponse(200, { recordId: RECORD_ID, operation: null })])
  expect(container.textContent).toContain('結果確認中の操作はありません。')
  expect(container.querySelector('[role="alert"]')).toBeNull()
})

it('取得できないときはエラーとして表示する', async () => {
  await inspect([
    jsonResponse(502, {
      error: { code: 'SUPPLEMENT_UNAVAILABLE', message: '補助情報を読み書きできませんでした。', requestId: 'r-1' },
    }),
  ])
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('補助情報を読み書きできませんでした。')
  expect(container.textContent).not.toContain('結果確認中の操作はありません。')
})

it('開始から間もない操作は回復できないと案内し、回復の操作を出さない', async () => {
  await inspect([
    jsonResponse(200, { recordId: RECORD_ID, operation: { ...target, recoverable: false } }),
  ])
  // 待ち時間は SPA に持たず、バックエンドが返す回復できる時刻を表示する
  expect(container.textContent).toContain('2026-11-09T09:05:00+00:00 を過ぎるまで回復できません')
  expect(container.querySelector('[name="recoveryReason"]')).toBeNull()
})

it('理由を付けて Registry を再取得して照合し、確定した結果と回復結果を表示する', async () => {
  const calls = await inspect([
    jsonResponse(200, { recordId: RECORD_ID, operation: target }),
    jsonResponse(200, {
      recordId: RECORD_ID,
      operationState: 'SUCCEEDED',
      message: 'Registry の状態から操作の成功を確認し、ロックを解除しました。',
      recovery: recovery('RECONCILED'),
    }),
  ])
  expect(button('Registry を再取得して照合').disabled).toBe(true)
  await setValue('recoveryReason', 'Registry を確認した')
  await click('Registry を再取得して照合')
  expect(calls[1]?.url).toBe(`/api/records/${RECORD_ID}/recovery`)
  expect(calls[1]?.init?.method).toBe('POST')
  expect(sentBody(calls, 1)).toEqual({ reason: 'Registry を確認した' })
  expect(container.textContent).toContain('成功')
  expect(container.textContent).toContain('Registry を再取得して確定')
  expect(container.textContent).toContain('回復した主体 admin-1')
})

it('照合で確定できなければ結果確認中のまま残し、強制解除は確認の後にだけ送る', async () => {
  const calls = await inspect([
    jsonResponse(200, { recordId: RECORD_ID, operation: target }),
    jsonResponse(202, {
      recordId: RECORD_ID,
      operationState: 'VERIFYING',
      message: '結果確認中です。',
      recovery: null,
    }),
    jsonResponse(200, {
      recordId: RECORD_ID,
      operationState: 'FAILED',
      message: '結果を確定できないまま、Admin が操作を強制解除しました。',
      recovery: recovery('RELEASED'),
    }),
  ])
  await setValue('recoveryReason', 'Registry を確認した')
  await click('Registry を再取得して照合')
  expect(container.textContent).toContain('結果確認中のままです')
  expect(button('強制解除する').disabled).toBe(true)
  await act(() => field('recoveryConfirm').click())
  await click('強制解除する')
  expect(sentBody(calls, 2)).toEqual({ reason: 'Registry を確認した', release: true })
  expect(container.textContent).toContain('失敗')
  expect(container.textContent).toContain('強制解除')
})

it('結果確認中のまま強制解除しようとして Registry が処理中なら、エラーとして表示する', async () => {
  await inspect([
    jsonResponse(200, { recordId: RECORD_ID, operation: target }),
    jsonResponse(202, { recordId: RECORD_ID, operationState: 'VERIFYING', message: '結果確認中です。', recovery: null }),
    jsonResponse(409, {
      error: { code: 'RECORD_STILL_PROCESSING', message: 'Registry がレコードを処理中のため強制解除できません。', requestId: 'r-3' },
    }),
  ])
  await setValue('recoveryReason', 'Registry を確認した')
  await click('Registry を再取得して照合')
  await act(() => field('recoveryConfirm').click())
  await click('強制解除する')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('強制解除できません。')
  expect(container.textContent).not.toContain('結果 失敗')
})

it('回復の失敗（409）はエラーとして表示し、結果を成功として表示しない', async () => {
  await inspect([
    jsonResponse(200, { recordId: RECORD_ID, operation: target }),
    jsonResponse(409, {
      error: { code: 'OPERATION_STILL_RUNNING', message: '操作の開始から5分が過ぎていないため回復できません。', requestId: 'r-2' },
    }),
  ])
  await setValue('recoveryReason', 'Registry を確認した')
  await click('Registry を再取得して照合')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('回復できません。')
  expect(container.textContent).not.toContain('成功')
})

it('回復できる時刻のない照会の応答は、形の違う応答として扱う', async () => {
  const withoutTime: Record<string, unknown> = { ...target }
  delete withoutTime.recoverableAt
  await inspect([jsonResponse(200, { recordId: RECORD_ID, operation: withoutTime })])
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(container.querySelector('[name="recoveryReason"]')).toBeNull()
})

it('AWS が受け付けた操作の強制解除の拒否（409）は、エラーとして表示しロックの解除を示さない', async () => {
  await inspect([
    jsonResponse(200, { recordId: RECORD_ID, operation: target }),
    jsonResponse(202, { recordId: RECORD_ID, operationState: 'VERIFYING', message: '結果確認中です。', recovery: null }),
    jsonResponse(409, {
      error: {
        code: 'ACCEPTED_BY_REGISTRY',
        message: 'Registry は操作を受け付けています。反映を待ってから、再度照合してください。',
        requestId: 'r-4',
      },
    }),
  ])
  await setValue('recoveryReason', 'Registry を確認した')
  await click('Registry を再取得して照合')
  await act(() => field('recoveryConfirm').click())
  await click('強制解除する')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('反映を待ってから')
  expect(container.textContent).not.toContain('結果 失敗')
})
