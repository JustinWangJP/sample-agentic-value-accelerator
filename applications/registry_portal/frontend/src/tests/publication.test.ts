// @vitest-environment happy-dom
// 公開管理（Seam 2: SPA の fetch 境界）。公開停止・廃止・廃止依頼の見送りの本文と、
// 実行できない理由・二重確認・409・結果が不明なときの再試行・結果確認中・入口（ID 指定とカタログ）の表示を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient, type CatalogItem } from '../api/client'
import { CatalogDetailView } from '../catalog/CatalogDetailView'
import { RecordPublicationPanel } from '../publication/RecordPublicationPanel'
import { RecordPublicationView } from '../publication/RecordPublicationView'
import { fakeFetch, jsonResponse } from './helpers'

const requestedBy = {
  issuer: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE',
  subject: 'publisher-1',
}
const openRequest = {
  requestId: 'request-1',
  recordId: 'agentABC1234',
  reason: '利用が終わったため',
  requestedBy,
  requestedAt: '2026-11-01T01:00:00+00:00',
  state: 'OPEN',
  resolution: null,
  resolutionReason: null,
  resolvedBy: null,
  resolvedAt: null,
}
const view = {
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '1.0.0',
  status: 'APPROVED',
  managed: true,
  ownerTeam: 'team-payments',
  publishedVersion: '1.0.0',
  suspended: false,
  operationId: null,
  deprecationRequest: openRequest,
  actions: {
    suspend: { allowed: true, message: null },
    deprecate: { allowed: true, message: null },
  },
}
const operation = {
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '1.0.0',
  ownerTeam: 'team-payments',
  operationId: 'decision-1',
  operationState: 'SUCCEEDED',
  submissionId: null,
  snapshotHash: null,
}
const suspended = { ...operation, status: 'REJECTED', publishedVersion: null }
const deprecated = { ...operation, status: 'DEPRECATED', publishedVersion: null }

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
async function submit(legend: string) {
  const form = [...container.querySelectorAll('form')].find(
    (element) => element.querySelector('legend')?.textContent === legend,
  )
  if (!form) throw new Error(`フォームがありません ${legend}`)
  await act(() => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}
async function confirmDeprecation() {
  const checkbox = container.querySelector<HTMLInputElement>('[name="deprecateConfirm"]')
  if (!checkbox) throw new Error('確認のチェックボックスがありません')
  await act(() => checkbox.click())
}
function apiFor(responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  return { api: createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' }), calls }
}
async function mountPanel(responses: (Response | Error)[], onChanged = vi.fn()) {
  const { api, calls } = apiFor(responses)
  await act(() =>
    root.render(
      createElement(RecordPublicationPanel, { api, recordId: 'agentABC1234', onRelogin: vi.fn(), onChanged }),
    ),
  )
  return calls
}
function sentBody(calls: { init?: RequestInit }[], index: number) {
  return JSON.parse(String(calls[index]?.init?.body))
}
function idempotencyKey(calls: { init?: RequestInit }[], index: number) {
  return new Headers(calls[index]?.init?.headers).get('idempotency-key')
}

it('現在の状態と未解決の廃止依頼を表示し、公開停止は理由を本文に載せて送る', async () => {
  const onChanged = vi.fn()
  const calls = await mountPanel([jsonResponse(200, view), jsonResponse(200, suspended)], onChanged)
  expect(calls[0]?.url).toBe('/api/records/agentABC1234/publication')
  expect(container.textContent).toContain('公開中の版 1.0.0')
  expect(container.textContent).toContain('理由: 利用が終わったため（依頼者 publisher-1')
  await setValue('suspendReason', '問題が見つかったため')
  // 理由の欄は操作ごとに分かれ、公開停止の理由が廃止に使われない
  expect(container.querySelector<HTMLTextAreaElement>('[name="deprecateReason"]')?.value).toBe('')
  await submit('公開停止')
  expect(calls[1]?.url).toBe('/api/records/agentABC1234/suspend')
  expect(sentBody(calls, 1)).toEqual({ reason: '問題が見つかったため' })
  expect(idempotencyKey(calls, 1)).toMatch(/^[A-Za-z0-9_-]+$/)
  expect(container.textContent).toContain('公開停止しました。廃止依頼があれば、廃止するか見送るかを判断してください。')
  expect(onChanged).toHaveBeenCalledOnce()
})

it('廃止は二重確認が必須で、確認のチェックとレコード名の入力を経てから confirm を付けて実行する', async () => {
  const calls = await mountPanel([jsonResponse(200, view), jsonResponse(200, deprecated)])
  await setValue('deprecateReason', '利用終了のため')
  expect(button('廃止する').disabled).toBe(true)
  expect(container.textContent).toContain('廃止は元に戻せません')
  // 1段目: 元に戻せないことを確認する。まだ送らず、最終確認に進む
  await confirmDeprecation()
  expect(button('廃止する').disabled).toBe(false)
  await submit('廃止')
  expect(calls).toHaveLength(1)
  const dialog = container.querySelector('[role="alertdialog"]')
  expect(dialog?.getAttribute('aria-label')).toBe('廃止の最終確認')
  expect(dialog?.textContent).toContain('決済 Agent（payments-a2a）を廃止します。')
  // 2段目: レコード名が一致するまで確定できない
  expect(button('廃止を確定する').disabled).toBe(true)
  await setValue('deprecateConfirmName', 'payments')
  expect(button('廃止を確定する').disabled).toBe(true)
  await setValue('deprecateConfirmName', 'payments-a2a')
  expect(button('廃止を確定する').disabled).toBe(false)
  await click('廃止を確定する')
  expect(calls[1]?.url).toBe('/api/records/agentABC1234/deprecate')
  expect(sentBody(calls, 1)).toEqual({ reason: '利用終了のため', confirm: true })
  expect(container.textContent).toContain('廃止しました。この操作は元に戻せません。')
  expect(container.querySelector('[role="alertdialog"]')).toBeNull()
})

it('廃止の最終確認をやめると、送らずに入力へ戻る', async () => {
  const calls = await mountPanel([jsonResponse(200, view)])
  await setValue('deprecateReason', '利用終了のため')
  await confirmDeprecation()
  await submit('廃止')
  await setValue('deprecateConfirmName', 'payments-a2a')
  await click('やめる')
  expect(container.querySelector('[role="alertdialog"]')).toBeNull()
  expect(calls).toHaveLength(1)
  // 理由と1段目の確認は残り、もう一度最終確認に進むときはレコード名を入力し直す
  expect(container.querySelector<HTMLTextAreaElement>('[name="deprecateReason"]')?.value).toBe('利用終了のため')
  await submit('廃止')
  expect(container.querySelector<HTMLInputElement>('[name="deprecateConfirmName"]')?.value).toBe('')
  expect(button('廃止を確定する').disabled).toBe(true)
})

it('実行できない操作は、理由を表示して送らせない', async () => {
  const message = '公開中の版 1.0.0 に加えて新しい版があるため、公開停止できません。'
  await mountPanel([
    jsonResponse(200, {
      ...view,
      status: 'PENDING_APPROVAL',
      actions: { ...view.actions, suspend: { allowed: false, message } },
    }),
  ])
  expect(container.textContent).toContain(message)
  await setValue('suspendReason', '問題が見つかったため')
  expect(button('公開停止する').disabled).toBe(true)
  expect(button('廃止する').disabled).toBe(true)
})

it('廃止依頼の見送りは、確認した依頼 ID と理由を送る', async () => {
  const onChanged = vi.fn()
  const calls = await mountPanel(
    [
      jsonResponse(200, { ...view, suspended: true, status: 'REJECTED', publishedVersion: null }),
      jsonResponse(200, {
        ...openRequest,
        state: 'RESOLVED',
        resolution: 'DECLINED',
        resolutionReason: '利用者が残っている',
        resolvedBy: { ...requestedBy, subject: 'curator-1' },
        resolvedAt: '2026-11-11T00:00:00+00:00',
      }),
    ],
    onChanged,
  )
  expect(container.textContent).toContain('公開停止中です')
  expect(container.textContent).toContain('公開停止済みです。影響を確かめてから')
  await setValue('declineReason', '利用者が残っている')
  await submit('依頼を見送る')
  expect(calls[1]?.url).toBe('/api/records/agentABC1234/deprecation-requests/decline')
  expect(sentBody(calls, 1)).toEqual({ requestId: 'request-1', reason: '利用者が残っている' })
  expect(container.textContent).toContain('廃止依頼を見送りました。')
  expect(onChanged).toHaveBeenCalledOnce()
})

it('409 は状態の変化を伝え、最新の状態を読み込み直せる', async () => {
  const calls = await mountPanel([
    jsonResponse(200, view),
    jsonResponse(409, {
      error: { code: 'CONTENT_CONFLICT', message: '別の操作が処理中です。', requestId: 'r-1' },
    }),
    jsonResponse(200, { ...view, operationId: 'other-operation' }),
  ])
  await setValue('suspendReason', '問題が見つかったため')
  await submit('公開停止')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('別の操作が処理中です。')
  expect(container.textContent).toContain('最新の状態を読み込んでから判断してください')
  await click('最新の状態を読み込む')
  expect(calls[2]?.url).toBe('/api/records/agentABC1234/publication')
  expect(container.textContent).toContain('別の操作が結果確認中です')
})

it('結果が不明な失敗のあとは、同じキーで再試行する', async () => {
  const calls = await mountPanel([
    jsonResponse(200, view),
    jsonResponse(502, {
      error: { code: 'REGISTRY_UNAVAILABLE', message: '取得できませんでした。', requestId: 'r-1' },
    }),
    jsonResponse(200, suspended),
  ])
  await setValue('suspendReason', '問題が見つかったため')
  await submit('公開停止')
  expect(container.textContent).toContain('結果が不明です。')
  await click('同じ内容で再試行')
  expect(idempotencyKey(calls, 2)).toBe(idempotencyKey(calls, 1))
  expect(container.textContent).toContain('公開停止しました。')
})

it('結果確認中の公開停止は、操作照会で確定する', async () => {
  const calls = await mountPanel([
    jsonResponse(200, view),
    jsonResponse(202, { operationId: 'decision-1', operationState: 'VERIFYING', message: '結果確認中です。' }),
    jsonResponse(200, suspended),
  ])
  await setValue('suspendReason', '問題が見つかったため')
  await submit('公開停止')
  expect(container.textContent).toContain('結果確認中です。')
  await click('決裁の結果を確認')
  expect(calls[2]?.url).toBe('/api/operations/decision-1')
  expect(container.textContent).toContain('公開停止しました。')
})

it('照会の応答に操作の可否がなければ、不正な応答として扱う', async () => {
  const withoutActions: Record<string, unknown> = { ...view }
  delete withoutActions.actions
  await mountPanel([jsonResponse(200, withoutActions)])
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    'API から想定外の応答が返されました。',
  )
})

it('Curator はレコード ID を指定して公開管理を開ける。Curator 以外には表示しない', async () => {
  const { api, calls } = apiFor([jsonResponse(200, { ...view, deprecationRequest: null })])
  await act(() =>
    root.render(createElement(RecordPublicationView, { api, roles: ['Publisher'], onRelogin: vi.fn() })),
  )
  expect(container.textContent).toBe('')
  await act(() =>
    root.render(createElement(RecordPublicationView, { api, roles: ['Curator'], onRelogin: vi.fn() })),
  )
  expect(button('公開管理を開く').disabled).toBe(true)
  await setValue('publicationRecordId', 'agentABC1234')
  const form = container.querySelector('form')
  await act(() => form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(calls[0]?.url).toBe('/api/records/agentABC1234/publication')
  expect(container.textContent).toContain('公開管理: 決済 Agent')
})

it('カタログの詳細は、Curator にだけ公開管理の入口を表示する', async () => {
  const item: CatalogItem = {
    recordId: 'agentABC1234',
    name: 'payments-a2a',
    displayName: '決済 Agent',
    description: '決済を調べる',
    recordType: 'AGENT',
    descriptorTypes: ['custom'],
    isA2A: false,
    recordVersion: '1.0.0',
    updatedAt: '2026-11-01T00:00:00+00:00',
  }
  const failure = jsonResponse(502, {
    error: { code: 'DISCOVERY_UNAVAILABLE', message: '取得できませんでした。', requestId: 'r-1' },
  })
  for (const canManagePublication of [false, true]) {
    const { api } = apiFor([failure.clone()])
    await act(() =>
      root.render(
        createElement(CatalogDetailView, {
          api,
          item,
          canViewHistory: false,
          canManagePublication,
          onRelogin: vi.fn(),
          onClose: vi.fn(),
        }),
      ),
    )
    const entry = [...container.querySelectorAll('button')].some(
      (element) => element.textContent === '公開管理を開く',
    )
    expect(entry).toBe(canManagePublication)
    await act(() => root.unmount())
    root = createRoot(container)
  }
})
