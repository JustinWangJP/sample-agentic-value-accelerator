// @vitest-environment happy-dom
// ログイン後の共通シェル（ヘッダー・業務ロール別のサイドバー・モバイルメニュー）と、画面のルーティング（Issue #85）

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StartupView } from '../App'
import { createApiClient, type Role } from '../api/client'
import { memoryHashLocation } from '../routing/location'
import { jsonResponse } from './helpers'

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

/** URL ごとに応答を返す fetch。画面の初回読み込み（カタログ・通知など）は順序が決まらないため URL で返す */
function apiByUrl() {
  const urls: string[] = []
  const fetchImpl: typeof fetch = async (input) => {
    const url = String(input)
    urls.push(url)
    if (url.startsWith('/api/catalog')) return jsonResponse(200, { items: [], nextToken: null })
    if (url.startsWith('/api/notifications')) return jsonResponse(200, { items: [], unreadCount: 0 })
    if (url.startsWith('/api/reviews')) return jsonResponse(200, { items: [], nextToken: null })
    return jsonResponse(404, { error: { code: 'NOT_FOUND', message: '想定外の呼び出し' } })
  }
  return { api: createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' }), urls }
}

async function mount(roles: Role[], hash = '', teams = ['team-payments']) {
  const { api, urls } = apiByUrl()
  const location = memoryHashLocation(hash)
  await act(async () =>
    root.render(
      createElement(StartupView, {
        location,
        state: {
          status: 'ready',
          me: { principal: { type: 'human', issuer: 'https://issuer.example.com', subject: 'sub-1' }, roles, teams },
          api,
          relogin: vi.fn(),
        },
      }),
    ),
  )
  return { location, urls }
}

function navLabels(label: string): string[] {
  const nav = container.querySelector(`nav[aria-label="${label}"]`)
  return [...(nav?.querySelectorAll('a') ?? [])].map((link) => link.textContent ?? '')
}

function heading(): string {
  return container.querySelector('main h1')?.textContent ?? ''
}

async function click(element: Element | null | undefined) {
  if (!(element instanceof HTMLElement)) throw new Error('要素が見つかりません')
  await act(async () => element.click())
}

describe('業務ロール別のサイドバー', () => {
  it('Viewer にはカタログだけを表示する', async () => {
    await mount(['Viewer'])
    expect(navLabels('メインナビゲーション')).toEqual(['カタログ'])
  })

  it('業務ロールに応じて、下書き・審査・公開管理・管理・回復の項目を加える', async () => {
    await mount(['Viewer', 'Publisher', 'Curator', 'Admin'])
    expect(navLabels('メインナビゲーション')).toEqual([
      'カタログ',
      '下書き・申請',
      '審査待ち',
      '公開管理',
      'Registry 管理',
      '回復操作',
    ])
  })

  it('表示中の画面の項目に aria-current を付け、クリックで画面を切り替える', async () => {
    const { location } = await mount(['Curator'])
    const current = () => container.querySelector('nav[aria-label="メインナビゲーション"] [aria-current="page"]')?.textContent
    expect(current()).toBe('カタログ')
    expect(heading()).toBe('カタログ')

    const reviews = [...container.querySelectorAll('nav[aria-label="メインナビゲーション"] a')].find(
      (link) => link.textContent === '審査待ち',
    )
    await click(reviews)
    expect(location.get()).toBe('#/reviews')
    expect(current()).toBe('審査待ち')
    expect(heading()).toBe('審査待ち')
  })
})

describe('画面のルーティング', () => {
  it('ログイン直後（ハッシュなし）と存在しないルートは、カタログ一覧を表示する', async () => {
    await mount(['Viewer'])
    expect(heading()).toBe('カタログ')
    await act(async () => root.unmount())
    root = createRoot(container)
    await mount(['Viewer'], '#/no-such-screen')
    expect(heading()).toBe('カタログ')
  })

  it('業務ロールのない画面を直接開くと、403 相当の表示にし、再ログイン・再試行のボタンは出さない', async () => {
    const { urls } = await mount(['Viewer'], '#/reviews')
    const alert = container.querySelector('main [role="alert"]')
    expect(heading()).toBe('審査待ち')
    expect(alert?.textContent).toContain('失敗（403 FORBIDDEN）')
    expect(alert?.textContent).toContain('この画面を利用する業務ロールがありません')
    expect(alert?.querySelectorAll('button')).toHaveLength(0)
    // 開けない画面の API は呼ばない
    expect(urls.some((url) => url.startsWith('/api/reviews'))).toBe(false)
  })

  it('Publisher でも所属チームがなければ、下書きの画面で所属チームが必要だと案内する', async () => {
    await mount(['Publisher'], '#/drafts', [])
    expect(container.querySelector('main')?.textContent).toContain('作成・編集には所属チームが必要です')
  })

  it('カタログの「下書きを作成」は Publisher にだけ表示し、作成画面へ進む', async () => {
    await mount(['Viewer'])
    expect(container.querySelector('main')?.textContent).not.toContain('＋ 下書きを作成')
    await act(async () => root.unmount())
    root = createRoot(container)

    const { location } = await mount(['Publisher'])
    const create = [...container.querySelectorAll('main a')].find((link) => link.textContent === '＋ 下書きを作成')
    await click(create)
    expect(location.get()).toBe('#/drafts/new')
    expect(heading()).toBe('下書きを作成')
  })
})

describe('ヘッダー', () => {
  it('自分の主体・業務ロール・所属チームは、ポップオーバーを開いたときだけ見せる', async () => {
    await mount(['Viewer', 'Publisher'])
    const trigger = container.querySelector('button[aria-label^="自分の主体・業務ロール・所属チーム"]')
    const panel = () => [...container.querySelectorAll('h2')].find((node) => node.textContent === '自分の主体・業務ロール・所属チーム')?.parentElement
    expect(trigger?.getAttribute('aria-expanded')).toBe('false')
    expect(panel()?.hidden).toBe(true)

    await click(trigger)
    expect(trigger?.getAttribute('aria-expanded')).toBe('true')
    expect(panel()?.hidden).toBe(false)
    expect(panel()?.textContent).toContain('https://issuer.example.com')
    expect(panel()?.textContent).toContain('Viewer、Publisher')
    expect(panel()?.textContent).toContain('team-payments')

    // 外側のクリックで閉じる
    await click(container.querySelector('main'))
    expect(panel()?.hidden).toBe(true)
  })

  it('モバイルメニューは同じ業務ロール別の項目を出し、項目を選ぶと閉じる', async () => {
    const { location } = await mount(['Admin'])
    await click(container.querySelector('button[aria-label="メニューを開く"]'))
    expect(navLabels('モバイルナビゲーション')).toEqual(['カタログ', 'Registry 管理', '回復操作'])

    const recovery = [...container.querySelectorAll('nav[aria-label="モバイルナビゲーション"] a')].find(
      (link) => link.textContent === '回復操作',
    )
    await click(recovery)
    expect(location.get()).toBe('#/recovery')
    expect(container.querySelector('nav[aria-label="モバイルナビゲーション"]')).toBeNull()
  })
})
