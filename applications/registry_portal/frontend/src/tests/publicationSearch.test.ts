// @vitest-environment happy-dom
// 管理レコードの検索一覧（Seam 2: SPA の fetch 境界）。名前の一部と状態での検索、公開停止中の区別、
// 一覧の項目から公開管理を開けること、0件・検索中・検索失敗の別の表示、ページングを確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { RecordPublicationView } from '../publication/RecordPublicationView'
import { fakeFetch, jsonResponse, type FetchCall } from './helpers'

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
/** 公開中のレコード（mcpPAY789012）の照会。一覧から公開停止を実行するときに使う */
const approvedPublicationView = {
  ...publicationView,
  recordId: 'mcpPAY789012',
  name: 'payments-mcp',
  displayName: '決済 MCP サーバー',
  recordType: 'MCP',
  recordVersion: '2.0.0',
  status: 'APPROVED',
  ownerTeam: 'team-settlement',
  publishedVersion: '2.0.0',
  suspended: false,
}
/** 公開停止の実行の結果（決裁 handler の操作。公開停止で REJECTED になり、発見できる版は null になる） */
const suspendOperation = {
  recordId: 'mcpPAY789012',
  name: 'payments-mcp',
  displayName: '決済 MCP サーバー',
  recordType: 'MCP',
  recordVersion: '2.0.0',
  status: 'REJECTED',
  ownerTeam: 'team-settlement',
  publishedVersion: null,
  operationId: 'decision-1',
  operationState: 'SUCCEEDED',
  submissionId: null,
  snapshotHash: null,
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
/** legend の文言でフォームを送る（公開管理のパネルの操作フォーム） */
async function submitLegend(legend: string) {
  const form = [...container.querySelectorAll('form')].find(
    (element) => element.querySelector('legend')?.textContent === legend,
  )
  if (!form) throw new Error(`フォームがありません ${legend}`)
  await act(() => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
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
/** 指定した範囲の中のボタンを返す（itemButton と同じ探し方の、一覧の外向け） */
function buttonIn(scope: ParentNode, label: string): HTMLButtonElement {
  const node = [...scope.querySelectorAll('button')].find(
    (element) => element.textContent === label,
  )
  if (!node) throw new Error(`ボタンがありません ${label}`)
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

it('0件は、nextToken の有無で別の文言を表示する', async () => {
  await search([jsonResponse(200, { items: [], nextToken: 'token-2' })])
  expect(container.textContent).toContain(
    'このページには条件に一致する管理レコードがありません。次のページを確認してください。',
  )
  expect(container.textContent).not.toContain('条件に一致する管理レコードはありません。')
})

it('最後のページの0件は、次のページがないことを伝える', async () => {
  await search([jsonResponse(200, { items: [], nextToken: null })])
  expect(container.textContent).toContain('条件に一致する管理レコードはありません。')
})

it('検索中は、結果とは別の状態として表示する', async () => {
  const calls: FetchCall[] = []
  // 応答が返らない fetch。検索中の表示だけを確かめる
  const fetchImpl: typeof fetch = (input, init) => {
    calls.push({ url: String(input), init })
    return new Promise<Response>(() => {})
  }
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  await act(() =>
    root.render(createElement(RecordPublicationView, { api, roles: ['Curator'], onRelogin: vi.fn() })),
  )
  await setValue('publicationSearchName', 'payment')
  await submit('publicationSearchName')
  expect(calls[0]?.url).toBe('/api/records/publication?name=payment')
  expect(container.querySelector('[role="status"]')?.textContent).toContain('検索しています')
})

it('検索の失敗は0件とは別のエラー表示にして、再試行できる', async () => {
  const calls = await search([
    jsonResponse(502, {
      error: { code: 'REGISTRY_UNAVAILABLE', message: '取得できませんでした。', requestId: 'r-1' },
    }),
    jsonResponse(200, { items: [suspendedItem], nextToken: null }),
  ])
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('取得できませんでした。')
  expect(container.textContent).not.toContain('条件に一致する管理レコードはありません。')
  await act(() => buttonIn(container, '再試行').click())
  expect(calls[1]?.url).toBe('/api/records/publication?name=payment&status=SUSPENDED')
  expect(listItem('決済 Agent').textContent).toContain('公開停止中')
})

it('次のページを同じ条件の nextToken で読み、最初のページに戻れる', async () => {
  const calls = await search([
    jsonResponse(200, { items: [suspendedItem], nextToken: 'token-2' }),
    jsonResponse(200, { items: [approvedItem], nextToken: null }),
    jsonResponse(200, { items: [suspendedItem], nextToken: null }),
  ])
  expect(buttonIn(container, '最初のページ').disabled).toBe(true)
  await act(() => buttonIn(container, '次のページ').click())
  expect(calls[1]?.url).toBe(
    '/api/records/publication?name=payment&status=SUSPENDED&nextToken=token-2',
  )
  expect(listItem('決済 MCP サーバー').textContent).toContain('発見できる版 2.0.0')
  expect(buttonIn(container, '次のページ').disabled).toBe(true)
  expect(buttonIn(container, '最初のページ').disabled).toBe(false)
  await act(() => buttonIn(container, '最初のページ').click())
  expect(calls[2]?.url).toBe('/api/records/publication?name=payment&status=SUSPENDED')
  expect(listItem('決済 Agent').textContent).toContain('公開停止中')
})

it('検索結果の項目に公開停止中の区別がなければ、不正な応答として扱う', async () => {
  const broken: Record<string, unknown> = { ...suspendedItem }
  delete broken.suspended
  await search([jsonResponse(200, { items: [broken], nextToken: null })])
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    'API から想定外の応答が返されました。',
  )
})

it('一覧から開いた公開管理の操作が済んだら、同じ条件で一覧を読み込み直す', async () => {
  const calls = await search(
    [
      jsonResponse(200, { items: [approvedItem], nextToken: null }),
      jsonResponse(200, approvedPublicationView),
      jsonResponse(200, suspendOperation),
      jsonResponse(200, {
        items: [{ ...approvedItem, status: 'REJECTED', suspended: true, publishedVersion: null }],
        nextToken: null,
      }),
    ],
    'payment',
    'APPROVED',
  )
  await act(() => itemButton('決済 MCP サーバー', '公開管理を開く').click())
  await setValue('suspendReason', '問題が見つかったため')
  await submitLegend('公開停止')
  expect(calls[2]?.url).toBe('/api/records/mcpPAY789012/suspend')
  expect(calls[3]?.url).toBe('/api/records/publication?name=payment&status=APPROVED')
  expect(listItem('決済 MCP サーバー').textContent).toContain('公開停止中')
})
