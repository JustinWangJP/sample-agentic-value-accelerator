// @vitest-environment happy-dom
// 共通部品（状態パネル・失敗の表示・履歴ドロワー）と、データ分類の表示ラベル（Issue #85）

import { act, createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from '../api/client'
import { DATA_CLASSIFICATIONS, DATA_CLASSIFICATION_LABELS } from '../api/dataClassification'
import { CatalogSearchView } from '../catalog/CatalogSearchView'
import { DetailBreadcrumb } from '../components/NavigationControls'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, ForbiddenState, LoadingState } from '../components/StatePanel'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import { fakeFetch, jsonResponse } from './helpers'

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

const html = (element: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(element)

describe('状態パネル', () => {
  it('0 件は正常な応答であることを示し、失敗や読み込み中と区別する', () => {
    const empty = html(createElement(EmptyState, { hint: '条件を変えてください。', children: '項目はありません。' }))
    expect(empty).toContain('0 件（正常応答）')
    expect(empty).toContain('項目はありません。')
    expect(empty).toContain('条件を変えてください。')
    expect(empty).not.toContain('role="alert"')

    const loading = html(createElement(LoadingState, { children: '読み込んでいます…' }))
    expect(loading).toMatch(/<p role="status"[^>]*>読み込んでいます…<\/p>/)
  })

  it('開けない画面（403 相当）は、再ログイン・再試行のボタンを出さない', () => {
    const forbidden = html(createElement(ForbiddenState, { screenLabel: '公開管理' }))
    expect(forbidden).toContain('role="alert"')
    expect(forbidden).toContain('失敗（403 FORBIDDEN）')
    expect(forbidden).toContain('公開管理を表示できません')
    expect(forbidden).not.toContain('<button')
  })
})

describe('失敗の表示', () => {
  const render = (error: ApiError, onRetry?: () => void) =>
    html(createElement(RequestErrorAlert, { error, onRetry, onRelogin: () => {} }))

  it('HTTP の状態・エラーコード・requestId を、メッセージと一緒に表示する', () => {
    const output = render(new ApiError(502, 'DISCOVERY_UNAVAILABLE', '取得できませんでした。', 'req-1'), () => {})
    expect(output).toContain('失敗（502 DISCOVERY_UNAVAILABLE）')
    expect(output).toContain('取得できませんでした。')
    expect(output).toContain('requestId: req-1')
    expect(output).toContain('再試行')
    expect(output).not.toContain('もう一度ログインする')
  })

  it('401 は再試行ではなく再ログインを、403 はどちらも出さない', () => {
    const unauthenticated = render(new ApiError(401, 'UNAUTHENTICATED', '認証が必要です。'), () => {})
    expect(unauthenticated).toContain('もう一度ログインする')
    expect(unauthenticated).not.toContain('再試行')

    // 再試行の操作を渡されても、403 は再試行しても解決しないためボタンを出さない
    const forbidden = render(new ApiError(403, 'FORBIDDEN', 'この操作に必要な業務ロールがありません。'), () => {})
    expect(forbidden).toContain('失敗（403 FORBIDDEN）')
    expect(forbidden).not.toContain('<button')
  })

  it.each(['A2A_SEARCH_NOT_READY', 'DRAFTS_NOT_CONFIGURED', 'NOTIFICATIONS_NOT_CONFIGURED'])(
    '準備中（503 %s）は失敗と区別し、再試行ボタンを出さない',
    (code) => {
      const output = render(new ApiError(503, code, '準備中です。'), () => {})
      expect(output).toContain(`準備中（503 ${code}）`)
      expect(output).toContain('bg-amber-50')
      expect(output).not.toContain('失敗（')
      expect(output).not.toContain('bg-rose-50')
      expect(output).not.toContain('<button')
    },
  )

  it('準備中でない 503 は失敗として扱い、再試行できる', () => {
    const output = render(new ApiError(503, 'APPROVAL_CONFIGURATION_CHANGED', '申請を停止しました。'), () => {})
    expect(output).toContain('失敗（503 APPROVAL_CONFIGURATION_CHANGED）')
    expect(output).toContain('再試行')
  })

  it('再試行ボタンの文言を変えられる', () => {
    const output = html(
      createElement(RequestErrorAlert, {
        error: new ApiError(502, 'DISCOVERY_UNAVAILABLE', '取得できませんでした。'),
        onRetry: () => {},
        retryLabel: '一覧を再読み込み',
        onRelogin: () => {},
      }),
    )
    expect(output).toContain('一覧を再読み込み')
    expect(output).not.toContain('>再試行<')
  })

  it('サーバーから応答がない失敗（status 0）は、状態のバッジを出さない', () => {
    const output = render(new ApiError(0, 'UNEXPECTED_ERROR', '想定外のエラーです。'))
    expect(output).not.toContain('失敗（')
    expect(output).toContain('想定外のエラーです。')
  })
})

describe('詳細画面のパンくず', () => {
  it('親の画面を省略すると親の名前をリンクにせず、戻るボタンの文言を変えられる', () => {
    const output = html(
      createElement(DetailBreadcrumb, {
        parentLabel: '下書き・申請',
        current: '申請',
        onBack: () => {},
        backLabel: '申請をやめる',
        backDisabled: true,
      }),
    )
    expect(output).toContain('下書き・申請')
    expect(output).not.toContain('<a')
    expect(output).toMatch(/<button[^>]*disabled=""[^>]*>申請をやめる<\/button>/)
  })
})

describe('操作と審査の履歴のドロワー', () => {
  it('開くとダイアログで履歴を表示し、閉じるボタンと Escape で閉じる', async () => {
    const { fetchImpl, calls } = fakeFetch([
      jsonResponse(200, { recordId: 'agentABC1234', ownerTeam: 'team-payments', entries: [] }),
      jsonResponse(200, { recordId: 'agentABC1234', ownerTeam: 'team-payments', entries: [] }),
    ])
    const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
    await act(async () =>
      root.render(createElement(RecordHistoryToggle, { api, recordId: 'agentABC1234', onRelogin: () => {} })),
    )
    expect(container.querySelector('[role="dialog"]')).toBeNull()

    await act(async () => container.querySelector('button')?.click())
    const dialog = container.querySelector('[role="dialog"]')
    expect(dialog?.textContent).toContain('操作と審査の履歴')
    expect(dialog?.textContent).toContain('recordId: agentABC1234')
    expect(dialog?.textContent).toContain('0 件（正常応答）')
    expect(calls).toHaveLength(1)

    await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="閉じる"]')?.click())
    expect(container.querySelector('[role="dialog"]')).toBeNull()

    await act(async () => container.querySelector('button')?.click())
    expect(container.querySelector('[role="dialog"]')).not.toBeNull()
    await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })
})

describe('データ分類の表示ラベル', () => {
  it('すべての値に補足付きのラベルがあり、送る値は変えない', () => {
    expect(DATA_CLASSIFICATIONS.map((value) => DATA_CLASSIFICATION_LABELS[value])).toEqual([
      'public（組織外公開可）',
      'private（組織内限定）',
    ])
    const api = createApiClient({ baseUrl: '', fetch: () => Promise.reject(new Error('呼ばれない')), getAccessToken: () => 't' })
    const output = html(
      createElement(CatalogSearchView, {
        api,
        onRelogin: () => {},
        onOpenDetail: () => {},
        onBrowseA2A: () => {},
        children: null,
      }),
    )
    expect(output).toContain('<option value="public">public（組織外公開可）</option>')
    expect(output).toContain('<option value="private">private（組織内限定）</option>')
  })
})
