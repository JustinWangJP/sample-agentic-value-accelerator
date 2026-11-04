// カタログ画面の状態ごとの表示を、静的な HTML に描画して検証する
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CatalogItem, CatalogPage } from '../api/client'
import { CatalogPanel } from '../catalog/CatalogView'
import { goNext, initialCatalogState, pageReceived, type CatalogState } from '../catalog/pager'

const noop = () => {}
const render = (state: CatalogState) =>
  renderToStaticMarkup(
    createElement(CatalogPanel, {
      state,
      onSelectType: noop,
      onNext: noop,
      onPrevious: noop,
      onRetry: noop,
      onRelogin: noop,
    }),
  )

const item = (overrides: Partial<CatalogItem> = {}): CatalogItem => ({
  recordId: 'rec-1',
  name: 'order-agent',
  displayName: '受注エージェント',
  description: '受注を処理する',
  recordType: 'AGENT',
  descriptorTypes: ['a2aAgentCard'],
  isA2A: true,
  recordVersion: '1.2.0',
  updatedAt: '2026-11-02T00:00:00Z',
  ...overrides,
})
const page = (items: CatalogItem[], nextToken: string | null = null): CatalogPage => ({
  items,
  nextToken,
})

describe('カタログ画面の表示', () => {
  it('Agent・MCP サーバー・Skill のタブを表示し、選択中の種別を示す', () => {
    const html = render(initialCatalogState('MCP'))

    expect(html).toContain('Agent')
    expect(html).toContain('MCP サーバー')
    expect(html).toContain('Skill')
    expect(html).toMatch(/aria-selected="true"[^>]*>MCP サーバー</)
    expect(html).toContain('読み込んでいます')
  })

  it('カタログ項目の表示名・名前・版・説明と、A2A かどうかを表示する', () => {
    const html = render(
      pageReceived(
        initialCatalogState('AGENT'),
        page([item(), item({ recordId: 'rec-2', name: 'plain', displayName: '', isA2A: false })]),
      ),
    )

    expect(html).toContain('受注エージェント')
    expect(html).toContain('order-agent')
    expect(html).toContain('1.2.0')
    expect(html).toContain('受注を処理する')
    expect(html.match(/A2A/g)).toHaveLength(1)
    // 表示名がなければ名前を見出しにする
    expect(html).toMatch(/<h3[^>]*>plain<\/h3>/)
  })

  it('公開承認は実行許可ではないことを明示する', () => {
    expect(render(initialCatalogState('AGENT'))).toContain('公開承認は実行許可ではありません')
  })

  it('次のページがあれば進め、1ページ目では前のページに戻れない', () => {
    const html = render(pageReceived(initialCatalogState('AGENT'), page([item()], 'token-2')))

    expect(html).toMatch(/<button[^>]*>前のページ<\/button>/)
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>前のページ<\/button>/)
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>次のページ<\/button>/)
    expect(html).toContain('ページ 1')
  })

  it('最後のページでは次のページへ進めない', () => {
    const state = pageReceived(
      goNext(pageReceived(initialCatalogState('AGENT'), page([item()], 'token-2'))),
      page([item({ recordId: 'rec-3' })]),
    )
    const html = render(state)

    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>次のページ<\/button>/)
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>前のページ<\/button>/)
    expect(html).toContain('ページ 2')
  })

  it('0件のときは、続きがなければ「ありません」、続きがあれば次のページを促す', () => {
    const none = render(pageReceived(initialCatalogState('SKILL'), page([])))
    const sparse = render(pageReceived(initialCatalogState('SKILL'), page([], 'token-2')))

    expect(none).toContain('公開承認済みの Skill はありません。')
    expect(sparse).toContain('このページには項目がありません。次のページを確認してください。')
    expect(sparse).not.toContain('公開承認済みの Skill はありません。')
  })

  it('取得の失敗は0件と区別して表示し、再試行できる', () => {
    const html = render({
      ...initialCatalogState('AGENT'),
      view: { status: 'error', message: 'カタログを取得できませんでした。', requiresLogin: false },
    })

    expect(html).toMatch(/role="alert"/)
    expect(html).toContain('カタログを取得できませんでした。')
    expect(html).toContain('再試行')
    expect(html).not.toContain('公開承認済みの Agent はありません。')
    expect(html).not.toContain('もう一度ログインする')
  })

  it('2ページ目以降で失敗したら、最初のページに戻れる（期限切れの token を再送し続けない）', () => {
    const onSecondPage = goNext(pageReceived(initialCatalogState('AGENT'), page([item()], 'token-2')))
    const error = { status: 'error', message: 'ページの指定が正しくありません。', requiresLogin: false } as const

    expect(render({ ...onSecondPage, view: error })).toContain('最初のページに戻る')
    expect(render({ ...initialCatalogState('AGENT'), view: error })).not.toContain('最初のページに戻る')
  })

  it('401 のときだけ、もう一度ログインするボタンを表示する', () => {
    const html = render({
      ...initialCatalogState('AGENT'),
      view: { status: 'error', message: '認証が必要です。', requiresLogin: true },
    })

    expect(html).toContain('もう一度ログインする')
  })
})
