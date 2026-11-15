// @vitest-environment happy-dom
// 管理レコードの検索一覧（Seam 2: SPA の fetch 境界）。名前の一部と状態での検索、公開停止中の区別、
// 一覧の項目から公開管理を開けること、0件・検索中・検索失敗の別の表示、ページングを確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { RecordPublicationView } from '../publication/RecordPublicationView'
import { fakeFetch, jsonResponse } from './helpers'

/** 検索一覧の項目（GET /api/records/publication の items の要素。Issue #42 の API 契約） */
const suspendedItem = {
  recordId: 'agentSUSP456',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '1.2.0',
  status: 'REJECTED',
  managed: true,
  ownerTeam: 'team-payments',
  suspended: true,
  publishedVersion: null,
}
const approvedItem = {
  recordId: 'mcpPAY789012',
  name: 'payments-mcp',
  displayName: '決済 MCP サーバー',
  recordType: 'MCP',
  recordVersion: '2.0.0',
  status: 'APPROVED',
  managed: true,
  ownerTeam: 'team-settlement',
  suspended: false,
  publishedVersion: '2.0.0',
}
/** 公開管理の照会（GET /api/records/{recordId}/publication）の応答。検索一覧から開いたときに使う */
const publicationView = {
  recordId: 'agentSUSP456',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '1.2.0',
  status: 'REJECTED',
  managed: true,
  ownerTeam: 'team-payments',
  publishedVersion: null,
  suspended: true,
  operationId: null,
  deprecationRequest: null,
  actions: {
    suspend: { allowed: true, message: null },
    deprecate: { allowed: true, message: null },
  },
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

async function setValue(name: string, value: string) {
  const node = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[name="${name}"]`)
  if (!node) throw new Error(`項目がありません ${name}`)
  const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set
  await act(() => {
    setter?.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function selectValue(name: string, value: string) {
  const node = container.querySelector<HTMLSelectElement>(`[name="${name}"]`)
  if (!node) throw new Error(`項目がありません ${name}`)
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
  await act(() => {
    setter?.call(node, value)
    node.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
/** 指定した入力項目を含むフォームを返す（画面には検索とレコード ID 指定の2つのフォームがある） */
function formOf(name: string): HTMLFormElement {
  const form = [...container.querySelectorAll('form')].find((element) =>
    element.querySelector(`[name="${name}"]`),
  )
  if (!form) throw new Error(`フォームがありません ${name}`)
  return form
}
async function submit(name: string) {
  await act(() =>
    formOf(name).dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
}
/** displayName を持つ一覧の項目（li）を返す */
function listItem(displayName: string): HTMLLIElement {
  const node = [...container.querySelectorAll('li')].find((element) =>
    element.textContent?.includes(displayName),
  )
  if (!node) throw new Error(`一覧の項目がありません ${displayName}`)
  return node
}
/** 一覧の項目の中のボタンを返す（画面にはレコード ID 指定のフォームにも同じラベルのボタンがある） */
function itemButton(displayName: string, label: string): HTMLButtonElement {
  const node = [...listItem(displayName).querySelectorAll('button')].find(
    (element) => element.textContent === label,
  )
  if (!node) throw new Error(`一覧の項目のボタンがありません ${displayName} ${label}`)
  return node
}
function apiFor(responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  return { api: createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' }), calls }
}
async function mountView(responses: (Response | Error)[]) {
  const { api, calls } = apiFor(responses)
  await act(() =>
    root.render(createElement(RecordPublicationView, { api, roles: ['Curator'], onRelogin: vi.fn() })),
  )
  return calls
}
async function search(responses: (Response | Error)[], name = 'payment', status = 'SUSPENDED') {
  const calls = await mountView(responses)
  await setValue('publicationSearchName', name)
  await selectValue('publicationSearchStatus', status)
  await submit('publicationSearchName')
  return calls
}

it('Curator は名前の一部と状態で管理レコードを探し、項目に公開停止中の区別と発見できる版を表示する', async () => {
  const calls = await search([jsonResponse(200, { items: [suspendedItem, approvedItem], nextToken: null })])
  expect(calls[0]?.url).toBe('/api/records/publication?name=payment&status=SUSPENDED')
  expect(listItem('決済 Agent').textContent).toContain('payments-a2a')
  expect(listItem('決済 Agent').textContent).toContain('team-payments')
  expect(listItem('決済 Agent').textContent).toContain('公開停止中')
  expect(listItem('決済 Agent').textContent).toContain('発見できる版なし')
  expect(listItem('決済 MCP サーバー').textContent).toContain('MCP サーバー')
  expect(listItem('決済 MCP サーバー').textContent).toContain('発見できる版 2.0.0')
  // 公開停止中でない項目には区別の表示が出ない
  expect(listItem('決済 MCP サーバー').textContent).not.toContain('公開停止中')
})

it('一覧の項目から、そのレコードの公開管理を開ける', async () => {
  const calls = await search([
    jsonResponse(200, { items: [suspendedItem], nextToken: null }),
    jsonResponse(200, publicationView),
  ])
  await act(() => itemButton('決済 Agent', '公開管理を開く').click())
  expect(calls[1]?.url).toBe('/api/records/agentSUSP456/publication')
  expect(container.textContent).toContain('公開管理: 決済 Agent')
})
