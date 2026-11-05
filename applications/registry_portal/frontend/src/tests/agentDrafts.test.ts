// @vitest-environment happy-dom
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { AgentDraftsView } from '../drafts/AgentDraftsView'
import { fakeFetch, jsonResponse } from './helpers'

const draft = {
  name: 'payments-agent',
  displayName: '決済 Agent',
  description: '決済の説明',
  recordVersion: '2.0.0',
  ownerTeam: 'team-payments',
  environment: 'dev',
  dataClassification: 'private',
  purpose: '決済を調べる',
  connectionReference: 'endpoint/payments',
  authentication: 'Cognito JWT',
  contact: 'payments@example.com',
  termsOfUse: '社内利用',
}
const detail = {
  recordId: 'agentABC1234',
  draft,
  contentHash: 'a'.repeat(64),
  status: 'DRAFT',
  hasDraft: true,
  publishedVersion: '1.0.0',
}
const item = {
  recordId: detail.recordId,
  displayName: draft.displayName,
  name: draft.name,
  recordVersion: '2.0.0',
  ownerTeam: draft.ownerTeam,
  status: 'UPDATE_FAILED',
  hasDraft: true,
  publishedVersion: '1.0.0',
  operationId: null,
}
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
async function fillDraft() {
  for (const [name, value] of Object.entries(draft)) {
    const input = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(
      `input[name="${name}"],textarea[name="${name}"]`,
    )
    if (!input || input.readOnly) continue
    const prototype =
      input instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set
    await act(() => {
      setter?.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
}
async function submit() {
  await act(() =>
    container
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
}
async function mount(
  responses: Response[],
  roles: ('Viewer' | 'Publisher')[] = ['Viewer', 'Publisher'],
  teams = ['team-payments'],
) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({
    baseUrl: '',
    fetch: fetchImpl,
    getAccessToken: () => 'publisher-token',
  })
  await act(() =>
    root.render(createElement(AgentDraftsView, { api, roles, teams, onRelogin: vi.fn() })),
  )
  return calls
}

it('一覧に下書き・公開中の版・AWS 失敗状態を併記する', async () => {
  await mount([jsonResponse(200, { items: [item], nextToken: null })])
  expect(container.textContent).toContain('下書きあり')
  expect(container.textContent).toContain('公開中 1.0.0')
  expect(container.textContent).toContain('UPDATE_FAILED')
})

it('AWS 作成タイムアウトの202から同じ本文とキーを再送して保存を完了する', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [], nextToken: null }),
    jsonResponse(202, {
      operationId: 'op-timeout', operationState: 'VERIFYING', message: '結果確認中です。',
    }),
    jsonResponse(201, { ...detail, operationId: 'op-timeout', operationState: 'SUCCEEDED' }),
  ])
  await click('Agent 下書きを作成')
  await fillDraft()
  await submit()
  expect(container.querySelector('fieldset')?.disabled).toBe(true)
  await click('同じ入力とキーで作成を再試行')
  expect(calls[2]?.url).toBe('/api/records')
  expect(calls[2]?.init?.method).toBe('POST')
  expect(calls[2]?.init?.body).toBe(calls[1]?.init?.body)
  expect(JSON.parse(String(calls[2]?.init?.body))).toEqual(draft)
  expect(new Headers(calls[2]?.init?.headers).get('idempotency-key')).toBe(
    new Headers(calls[1]?.init?.headers).get('idempotency-key'),
  )
  expect(container.textContent).toContain('下書きを保存しました。')
})

it.each(['CREATE', 'UPDATE'])('不明な%sは入力を止め、失敗確定後に修正して保存できる', async (kind) => {
  const calls = await mount([
    jsonResponse(200, { items: kind === 'CREATE' ? [] : [item], nextToken: null }),
    ...(kind === 'UPDATE' ? [jsonResponse(200, detail)] : []),
    jsonResponse(202, { operationId: 'op-pending', operationState: 'VERIFYING', message: '結果確認中です。' }),
    jsonResponse(200, { operationId: 'op-pending', operationState: 'FAILED', message: '操作は失敗しました。内容を確認して再実行してください。' }),
    jsonResponse(200, { ...detail, operationId: 'op-retry', operationState: 'SUCCEEDED' }),
  ])
  await click(kind === 'CREATE' ? 'Agent 下書きを作成' : '編集')
  if (kind === 'CREATE') await fillDraft()
  await submit()
  const before = calls.length
  await submit()
  expect(calls).toHaveLength(before)
  expect(container.querySelector('fieldset')?.disabled).toBe(true)
  if (kind === 'UPDATE') expect(container.textContent).not.toContain('同じ入力とキーで作成を再試行')
  await click('処理結果を確認')
  expect(container.textContent).toContain('操作は失敗しました。')
  expect(container.querySelector('fieldset')?.disabled).toBe(false)
  await submit()
  const last = calls.at(-1)
  expect(last?.init?.method).toBe(kind === 'CREATE' ? 'POST' : 'PATCH')
  expect(container.textContent).toContain('下書きを保存しました。')
  if (kind === 'CREATE') {
    expect(new Headers(last?.init?.headers).get('idempotency-key')).not.toBe(
      new Headers(calls[1]?.init?.headers).get('idempotency-key'),
    )
  }
})

it('更新のHTTP応答を失った場合は再送せず一覧から操作を確認する', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [item], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(502, { error: { code: 'GATEWAY_ERROR', message: '通信が途切れました。' } }),
    jsonResponse(200, { items: [{ ...item, operationId: 'unknown-update' }], nextToken: null }),
  ])
  await click('編集')
  await submit()
  expect(container.querySelector('fieldset')?.disabled).toBe(true)
  expect(button('下書きを保存').disabled).toBe(true)
  expect(container.textContent).toContain('更新結果が不明')
  expect(container.textContent).not.toContain('同じ入力とキーで作成を再試行')
  await submit()
  expect(calls).toHaveLength(3)
  await click('一覧に戻る')
  expect(container.textContent).toContain('処理結果を確認')
})

it('409 の後は入力を残し、明示的な再読込で最新内容とハッシュを取得する', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [item], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(409, { error: { code: 'CONTENT_CONFLICT', message: '内容が変更されました。' } }),
    jsonResponse(200, {
      ...detail,
      contentHash: 'b'.repeat(64),
      draft: { ...draft, description: '他の人の新しい説明' },
    }),
  ])
  await click('編集')
  await act(() =>
    container
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
  expect(calls[2]?.init?.method).toBe('PATCH')
  expect(JSON.parse(String(calls[2]?.init?.body))).toEqual({
    draft,
    expectedContentHash: 'a'.repeat(64),
  })
  expect(container.textContent).toContain('最新の内容を再読み込み')
  expect(container.querySelector<HTMLTextAreaElement>('[name="description"]')?.value).toBe(
    '決済の説明',
  )
  await click('最新の内容を再読み込み')
  expect(calls[3]?.url).toBe('/api/records/agentABC1234')
  expect(container.querySelector<HTMLTextAreaElement>('[name="description"]')?.value).toBe(
    '他の人の新しい説明',
  )
})

it('Viewer とチームなし Publisher には作成フォームを出さない', async () => {
  const calls = await mount([], ['Viewer'])
  expect(container.textContent).not.toContain('Agent 下書きの管理')
  expect(calls).toHaveLength(0)
  await mount([], ['Viewer', 'Publisher'], [])
  expect(container.textContent).toContain('所属チームが必要')
  expect(container.querySelector('form')).toBeNull()
})

it('作成の通信結果が不明でも同じ Idempotency-Key と入力で再試行し、結果確認中は編集を止める', async () => {
  const { fetchImpl, calls } = fakeFetch([
    jsonResponse(200, { items: [], nextToken: null }),
    new Error('通信断'),
    jsonResponse(202, {
      operationId: 'op-1',
      operationState: 'VERIFYING',
      message: '結果確認中です。',
    }),
    jsonResponse(200, { ...detail, operationId: 'op-1', operationState: 'SUCCEEDED' }),
    jsonResponse(200, { items: [item], nextToken: null }),
  ])
  const api = createApiClient({
    baseUrl: '',
    fetch: fetchImpl,
    getAccessToken: () => 'publisher-token',
  })
  await act(() =>
    root.render(
      createElement(AgentDraftsView, {
        api,
        roles: ['Publisher'],
        teams: ['team-payments'],
        onRelogin: vi.fn(),
      }),
    ),
  )
  await click('Agent 下書きを作成')
  await fillDraft()
  await act(() =>
    container
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
  expect(container.textContent).toContain('作成結果が不明')
  expect(container.querySelector('fieldset')?.disabled).toBe(true)
  const firstKey = new Headers(calls[1]?.init?.headers).get('idempotency-key')
  expect(firstKey).toMatch(/^[a-f0-9-]{36}$/)
  await act(() =>
    container
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
  expect(new Headers(calls[2]?.init?.headers).get('idempotency-key')).toBe(firstKey)
  expect(JSON.parse(String(calls[2]?.init?.body))).toEqual(draft)
  expect(container.textContent).toContain('結果確認中')
  expect(container.querySelector('fieldset')?.disabled).toBe(true)
  await click('処理結果を確認')
  expect(calls[3]?.url).toBe('/api/operations/op-1')
  expect(container.textContent).toContain('下書きあり')
})

it.each([
  'Registry に接続できません。',
  '保存された管理情報の形式が正しくありません。管理者に連絡してください。',
])('一覧の障害「%s」を0件表示に置き換えず、再取得で回復する', async (message) => {
  await mount([
    jsonResponse(502, {
      error: { code: 'REGISTRY_UNAVAILABLE', message },
    }),
    jsonResponse(200, { items: [], nextToken: null }),
  ])
  expect(container.textContent).toContain(message)
  expect(container.textContent).not.toContain('所属チームの Agent はありません。')
  await click('一覧を再読み込み')
  expect(container.textContent).toContain('所属チームの Agent はありません。')
})
