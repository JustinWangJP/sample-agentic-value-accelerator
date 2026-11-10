// @vitest-environment happy-dom
// 操作履歴の表示（Seam 2: SPA の fetch 境界）。
// API の応答の検査、操作 ID・主体・対象ハッシュ・時刻・結果・AWS request ID の表示、
// 取り込み前から公開されていた項目の「不明」、0件・権限なし・未管理の区別、
// 結果確認中の決裁の伏せた表示、Curator・Admin の画面からの導線を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { CatalogDetailView } from '../catalog/CatalogDetailView'
import { canViewAllHistory } from '../drafts/api'
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
    principal: { issuer: 'https://issuer', subject: 'alice' },
    startedAt: '2026-11-01T00:00:00+00:00',
    targetHash: 'a'.repeat(64),
    state: 'SUCCEEDED',
    awsRequestId: 'aws-create-1',
  },
  {
    operationId: null,
    kind: 'APPROVE',
    principal: null,
    startedAt: null,
    targetHash: null,
    state: 'UNKNOWN',
    recordVersion: '0.9.0',
  },
  {
    operationId: 'approve-op',
    kind: 'APPROVE',
    principal: { issuer: 'https://issuer', subject: 'carol' },
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
  // 旧い結果の値（IN_PROGRESS）や、必須の項目を省いた応答は受け付けない
  const oldState = client([
    jsonResponse(200, { ...historyBody, entries: [{ ...entries[0], state: 'IN_PROGRESS' }] }),
  ])
  await expect(oldState.api.getHistory('agentABC1234')).rejects.toThrow('想定外の応答')
  const withoutHash: Record<string, unknown> = { ...entries[0] }
  delete withoutHash.targetHash
  const omitted = client([jsonResponse(200, { ...historyBody, entries: [withoutHash] })])
  await expect(omitted.api.getHistory('agentABC1234')).rejects.toThrow('想定外の応答')
})

it('操作と審査の履歴を、項目ごとの操作 ID・主体・対象ハッシュ・時刻・結果・AWS request ID 付きで表示する', async () => {
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
  // 主体は issuer と subject の組で表示する
  expect(text(0)).toContain('alice（https://issuer）')
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
    { ...entries[2], operationId: 'open-op', state: 'VERIFYING', awsRequestId: undefined },
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

async function renderHistory(response: Response) {
  const { api } = client([response])
  await act(async () => {
    root.render(createElement(RecordHistoryView, { api, recordId: 'agentABC1234', onRelogin: () => {} }))
  })
}

it('操作記録が0件なら、取得できなかった場合と区別して「記録なし」と表示する', async () => {
  await renderHistory(jsonResponse(200, { ...historyBody, entries: [] }))
  expect(container.textContent).toContain('操作の記録はありません。')
  expect(container.querySelector('[role="alert"]')).toBeNull()
})

it('権限のない履歴（403）と管理下にないレコード（404）は、0件と区別してエラーを表示する', async () => {
  await renderHistory(
    jsonResponse(403, { error: { code: 'FORBIDDEN', message: 'この操作を行う権限がありません。' } }),
  )
  expect(container.textContent).toContain('この操作を行う権限がありません。')
  expect(container.textContent).not.toContain('操作の記録はありません。')

  await act(() => root.unmount())
  root = createRoot(container)
  await renderHistory(
    jsonResponse(404, {
      error: { code: 'RECORD_NOT_FOUND', message: '指定された Registry レコードの操作履歴はありません。' },
    }),
  )
  expect(container.textContent).toContain('指定された Registry レコードの操作履歴はありません。')
  expect(container.textContent).not.toContain('操作の記録はありません。')
})

it('決裁した Curator 以外に伏せられた結果確認中の決裁は、内容を表示せずその旨を示す', async () => {
  const withheld = {
    operationId: null,
    kind: 'APPROVE',
    principal: { issuer: 'https://issuer', subject: 'carol' },
    startedAt: '2026-11-03T00:00:00+00:00',
    targetHash: null,
    state: 'VERIFYING',
    withheld: true,
  }
  await renderHistory(jsonResponse(200, { ...historyBody, entries: [withheld] }))
  const item = container.querySelector('ul.history-entries li')
  expect(item?.textContent).toContain('公開承認・結果確認中')
  expect(item?.textContent).toContain('carol')
  expect(item?.textContent).toContain('決裁した Curator 本人にだけ表示します')
  expect(item?.textContent).not.toContain('操作 ID')
})

it('カタログ詳細から開いた履歴で、管理下にないレコードは404の案内を表示する', async () => {
  const item = {
    recordId: 'agentABC1234',
    name: 'payments-agent',
    displayName: '決済エージェント',
  } as Parameters<typeof CatalogDetailView>[0]['item']
  const { api } = client([
    jsonResponse(502, { error: { code: 'DISCOVERY_UNAVAILABLE', message: '取得できません。' } }),
    jsonResponse(404, {
      error: { code: 'RECORD_NOT_FOUND', message: '指定された Registry レコードの操作履歴はありません。' },
    }),
  ])
  await act(async () => {
    root.render(
      createElement(CatalogDetailView, { item, api, canViewHistory: true, onRelogin: () => {}, onClose: () => {} }),
    )
  })
  const button = [...container.querySelectorAll('button')].find(
    (candidate) => candidate.textContent === '操作と審査の履歴を表示',
  )
  await act(async () => button?.click())
  expect(container.textContent).toContain('指定された Registry レコードの操作履歴はありません。')
})

it('全レコードの履歴を読めるのは Curator と Admin だけ', () => {
  expect(canViewAllHistory(['Viewer', 'Curator'])).toBe(true)
  expect(canViewAllHistory(['Viewer', 'Admin'])).toBe(true)
  expect(canViewAllHistory(['Viewer', 'Publisher'])).toBe(false)
  expect(canViewAllHistory([])).toBe(false)
})

it('Admin の回復操作で確定した操作は、回復の方法・主体・理由を表示し、形の崩れた回復結果は受け付けない', async () => {
  const recovered = {
    ...entries[2],
    operationId: 'recovered-op',
    state: 'FAILED',
    errorCode: 'ADMIN_RELEASED',
    recovery: {
      mode: 'RELEASED',
      by: { issuer: 'https://issuer', subject: 'admin-1' },
      at: '2026-11-03T00:00:00+00:00',
      reason: 'Registry を確認した',
    },
  }
  const { api } = client([jsonResponse(200, { ...historyBody, entries: [recovered] })])
  await act(async () => {
    root.render(createElement(RecordHistoryView, { api, recordId: 'agentABC1234', onRelogin: () => {} }))
  })
  const text = container.querySelector('ul.history-entries li')?.textContent ?? ''
  expect(text).toContain('失敗（ADMIN_RELEASED）')
  expect(text).toContain('確定できないまま強制解除')
  expect(text).toContain('回復した主体 admin-1')
  expect(text).toContain('回復の理由 Registry を確認した')
  const broken = client([
    jsonResponse(200, { ...historyBody, entries: [{ ...recovered, recovery: { mode: 'UNKNOWN' } }] }),
  ])
  await expect(broken.api.getHistory('agentABC1234')).rejects.toThrow('想定外の応答')
})
