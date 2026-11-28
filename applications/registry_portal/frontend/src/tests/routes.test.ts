// 画面のルート（URL のハッシュ）の解釈・組み立てと、業務ロールによる画面の可否
import { describe, expect, it } from 'vitest'
import { canOpenScreen, DEFAULT_ROUTE, formatRoute, parseRoute, type Route } from '../routing/routes'

describe('ルートの解釈と組み立て', () => {
  const routes: [string, Route][] = [
    ['#/catalog', { screen: 'catalog', recordId: null }],
    ['#/catalog/agentAbc1234', { screen: 'catalog', recordId: 'agentAbc1234' }],
    ['#/drafts', { screen: 'drafts', view: { kind: 'list' } }],
    ['#/drafts/new', { screen: 'drafts', view: { kind: 'new' } }],
    ['#/drafts/agentAbc1234', { screen: 'drafts', view: { kind: 'edit', recordId: 'agentAbc1234' } }],
    ['#/drafts/agentAbc1234/submit', { screen: 'drafts', view: { kind: 'submit', recordId: 'agentAbc1234' } }],
    ['#/reviews', { screen: 'reviews', recordId: null }],
    ['#/reviews/agentAbc1234', { screen: 'reviews', recordId: 'agentAbc1234' }],
    ['#/publication', { screen: 'publication' }],
    ['#/administration', { screen: 'administration' }],
    ['#/recovery', { screen: 'recovery' }],
  ]

  it.each(routes)('%s を解釈し、同じハッシュに組み立て直せる', (hash, route) => {
    expect(parseRoute(hash)).toEqual(route)
    expect(formatRoute(route)).toBe(hash)
  })

  it('ハッシュがない・ルートだけのときは、カタログ一覧にする（ログイン直後のコールバックはルートに戻る）', () => {
    expect(parseRoute('')).toEqual(DEFAULT_ROUTE)
    expect(parseRoute('#')).toEqual(DEFAULT_ROUTE)
    expect(parseRoute('#/')).toEqual(DEFAULT_ROUTE)
    expect(DEFAULT_ROUTE).toEqual({ screen: 'catalog', recordId: null })
  })

  it.each(['#/unknown', '#catalog', '#/catalog/a/b', '#/drafts/new/extra', '#/drafts/a/edit', '#/publication/x', '#/catalog/%E0%A4%A'])(
    '存在しないルート %s は解釈しない（呼び出し側でカタログ一覧にする）',
    (hash) => {
      expect(parseRoute(hash)).toBeNull()
    },
  )

  it('レコード ID は URL エンコードして組み立て、解釈するときに戻す', () => {
    const route: Route = { screen: 'catalog', recordId: 'a b' }
    expect(formatRoute(route)).toBe('#/catalog/a%20b')
    expect(parseRoute('#/catalog/a%20b')).toEqual(route)
  })
})

describe('業務ロールによる画面の可否', () => {
  it('カタログは全員、下書きは Publisher、審査・公開管理は Curator、管理・回復は Admin が開ける', () => {
    expect(canOpenScreen('catalog', ['Viewer'])).toBe(true)
    expect(canOpenScreen('drafts', ['Viewer'])).toBe(false)
    expect(canOpenScreen('drafts', ['Publisher'])).toBe(true)
    expect(canOpenScreen('reviews', ['Publisher'])).toBe(false)
    expect(canOpenScreen('reviews', ['Curator'])).toBe(true)
    expect(canOpenScreen('publication', ['Curator'])).toBe(true)
    expect(canOpenScreen('administration', ['Curator'])).toBe(false)
    expect(canOpenScreen('administration', ['Admin'])).toBe(true)
    expect(canOpenScreen('recovery', ['Admin'])).toBe(true)
  })
})
