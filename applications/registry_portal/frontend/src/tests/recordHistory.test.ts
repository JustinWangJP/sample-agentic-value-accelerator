// @vitest-environment happy-dom
// 操作履歴の表示（Seam 2: SPA の fetch 境界）。
// API の応答の検査、操作 ID・操作者・対象ハッシュ・時刻・結果・AWS request ID の表示、
// 取り込み前から公開されていた項目の「不明」、Curator・Admin の画面からの導線を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { CatalogDetailView } from '../catalog/CatalogDetailView'
import { RecordHistoryToggle, RecordHistoryView } from '../drafts/RecordHistoryView'
import { fakeFetch, jsonResponse, TEST_CONFIG } from './helpers'

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

const entries = [
  {
    operationId: 'create-op',
    kind: 'CREATE',
    operator: { issuer: 'https://issuer', subject: 'alice' },
    startedAt: '2026-11-01T00:00:00+00:00',
    targetHash: 'a'.repeat(64),
    state: 'SUCCEEDED',
    awsRequestId: 'aws-create-1',
  },
  {
    operationId: null,
    kind: 'APPROVE',
    operator: null,
    startedAt: null,
    targetHash: null,
    state: 'UNKNOWN',
    recordVersion: '0.9.0',
  },
  {
    operationId: 'approve-op',
    kind: 'APPROVE',
    operator: { issuer: 'https://issuer', subject: 'carol' },
    startedAt: '2026-11-02T00:00:00+00:00',
    targetHash: 'b'.repeat(64),
    state: 'SUCCEEDED',
    awsRequestId: 'aws-decide-1',
    reason: '利用申請の内容を確認した',
  },
]
const historyBody = { recordId: 'agentABC1234', ownerTeam: 'team-payments', entries }

function client(responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({
    baseUrl: TEST_CONFIG.apiBaseUrl,
    fetch: fetchImpl,
    getAccessToken: () => 'token',
  })
  return { api, calls }
}

it('履歴 API の応答を検査して扱う', async () => {
  const good = client([jsonResponse(200, historyBody)])
  const history = await good.api.getHistory('agentABC1234')
  expect(history.entries).toHaveLength(3)
  expect(good.calls[0].url).toBe(`${TEST_CONFIG.apiBaseUrl}/api/records/agentABC1234/history`)
  const bad = client([jsonResponse(200, { recordId: 'agentABC1234', entries: 'x' })])
  await expect(bad.api.getHistory('agentABC1234')).rejects.toThrow('想定外の応答')
})

it('操作と審査の履歴を、項目ごとの操作 ID・操作者・対象ハッシュ・時刻・結果・AWS request ID 付きで表示する', async () => {
  const { api } = client([jsonResponse(200, historyBody)])
  await act(async () => {
    root.render(
      createElement(RecordHistoryView, {
        api,
        recordId: 'agentABC1234',
        onRelogin: () => {},
      }),
    )
  })
  const list = container.querySelector('ul.history-entries')
  expect(list).not.toBeNull()
  const items = [...(list?.querySelectorAll('li') ?? [])]
  expect(items).toHaveLength(3)
  const text = (i: number) => items[i].textContent ?? ''
  expect(text(0)).toContain('作成')
  expect(text(0)).toContain('成功')
  expect(text(0)).toContain('alice')
  expect(text(0)).toContain('aws-create-1')
  // 公開承認した主体がわからない項目は「不明」と表示する
  expect(text(1)).toContain('不明')
  expect(text(1)).toContain('版 0.9.0')
  // 決裁の理由も審査の履歴として表示する
  expect(text(2)).toContain('利用申請の内容を確認した')
})

it('履歴の取得に失敗したときは日本語のエラーを表示する', async () => {
  const { api } = client([
    jsonResponse(502, { error: { code: 'HISTORY_UNAVAILABLE', message: '操作履歴を取得できませんでした。' } }),
  ])
  await act(async () => {
    root.render(
      createElement(RecordHistoryView, {
        api,
        recordId: 'agentABC1234',
        onRelogin: () => {},
      }),
    )
  })
  expect(container.textContent).toContain('操作履歴を取得できませんでした。')
})

it('失敗はエラーコード付き、結果の確定していない操作は「結果確認中」と表示する', async () => {
  const unsettled = [
    { ...entries[0], operationId: 'failed-op', state: 'FAILED', errorCode: 'AWS_REJECTED' },
    { ...entries[2], operationId: 'open-op', state: 'IN_PROGRESS', awsRequestId: undefined },
  ]
  const { api } = client([jsonResponse(200, { ...historyBody, entries: unsettled })])
  await act(async () => {
    root.render(createElement(RecordHistoryView, { api, recordId: 'agentABC1234', onRelogin: () => {} }))
  })
  const items = [...container.querySelectorAll('ul.history-entries li')]
  expect(items[0].textContent).toContain('失敗（AWS_REJECTED）')
  expect(items[1].textContent).toContain('結果確認中')
})

it('履歴の開閉ボタンを押すまで履歴 API を呼ばない', async () => {
  const { api, calls } = client([jsonResponse(200, historyBody)])
  await act(async () => {
    root.render(createElement(RecordHistoryToggle, { api, recordId: 'agentABC1234', onRelogin: () => {} }))
  })
  expect(calls).toHaveLength(0)
  const button = container.querySelector('button')
  await act(async () => button?.click())
  expect(calls[0].url).toBe(`${TEST_CONFIG.apiBaseUrl}/api/records/agentABC1234/history`)
  expect(container.querySelectorAll('ul.history-entries li')).toHaveLength(3)
  expect(button?.textContent).toBe('履歴を閉じる')
})

it('カタログ詳細の履歴の導線は、Curator・Admin にだけ表示する', async () => {
  const item = {
    recordId: 'agentABC1234',
    name: 'payments-agent',
    displayName: '決済エージェント',
  } as Parameters<typeof CatalogDetailView>[0]['item']
  const detailError = () =>
    jsonResponse(502, { error: { code: 'DISCOVERY_UNAVAILABLE', message: '取得できません。' } })
  const props = { item, onRelogin: () => {}, onClose: () => {} }
  const hidden = client([detailError()])
  await act(async () => {
    root.render(createElement(CatalogDetailView, { ...props, api: hidden.api, canViewHistory: false }))
  })
  expect(container.textContent).not.toContain('操作と審査の履歴を表示')
  const shown = client([detailError()])
  await act(async () => {
    root.render(
      createElement(CatalogDetailView, { ...props, key: 'curator', api: shown.api, canViewHistory: true }),
    )
  })
  expect(container.textContent).toContain('操作と審査の履歴を表示')
})
