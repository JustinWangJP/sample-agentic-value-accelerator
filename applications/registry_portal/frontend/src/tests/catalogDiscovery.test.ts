// @vitest-environment happy-dom

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, type CatalogDetail, type CatalogItem } from '../api/client'
import { CatalogView } from '../catalog/CatalogView'
import { fakeFetch, jsonResponse } from './helpers'

const agent: CatalogItem = {
  recordId: 'agentAbc1234',
  name: 'orders-agent',
  displayName: '受注エージェント',
  description: '受注を処理する',
  recordType: 'AGENT',
  descriptorTypes: ['a2aAgentCard'],
  isA2A: true,
  recordVersion: '1.2.3',
  updatedAt: '2026-11-02T00:00:00Z',
}
const skill: CatalogItem = {
  ...agent,
  recordId: 'skillAbc1234',
  name: 'orders-skill',
  displayName: '受注スキル',
  recordType: 'SKILL',
  descriptorTypes: ['agentSkillsDefinition'],
  isA2A: false,
}
const mcp: CatalogItem = {
  ...agent,
  recordId: 'mcpAbc123456',
  name: 'stock-mcp',
  displayName: '在庫 MCP',
  recordType: 'MCP',
  descriptorTypes: ['mcpServer'],
  isA2A: false,
}

function found(...items: CatalogItem[]) {
  return jsonResponse(200, { items, inconsistentA2ACount: 0 })
}

function detailOf(item: CatalogItem, overrides: Partial<CatalogDetail> = {}): CatalogDetail {
  return {
    ...item,
    definition: { a2aAgentCard: { data: '{"name":"orders-agent"}' } },
    ownerTeam: 'team-orders',
    contact: 'orders@example.com',
    termsOfUse: '開発環境のみ',
    connectionReference: 'agent-endpoint/orders',
    dataClassification: 'private',
    skillMarkdown: '',
    relatedReferences: [{ recordId: 'skillAbc1234', recordVersion: '2.3.4' }],
    ...overrides,
  }
}

function errorResponse(status: number, code: string, message: string) {
  return jsonResponse(status, { error: { code, message, requestId: 'req-1' } })
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

async function mount(...responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch([jsonResponse(200, { items: [agent], nextToken: null }), ...responses])
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'viewer-token' })
  const relogin = vi.fn()
  await act(() => root.render(createElement(CatalogView, { api, onRelogin: relogin })))
  return { calls, relogin }
}

function element<T extends Element>(selector: string): T {
  const node = container.querySelector<T>(selector)
  if (!node) throw new Error(`要素が見つかりません: ${selector}`)
  return node
}

function buttonNames(scope: Element): string[] {
  return [...scope.querySelectorAll('button')].map((button) => button.textContent?.trim() ?? '')
}

async function changeSelect(name: string, value: string) {
  const select = element<HTMLSelectElement>(`select[name="${name}"]`)
  await act(() => {
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

async function search(query: string) {
  element<HTMLInputElement>('input[name="query"]').value = query
  await act(() => element<HTMLFormElement>('form').requestSubmit())
}

async function clickButton(name: string) {
  const button = [...container.querySelectorAll('button')].find(
    (candidate) => candidate.textContent?.trim() === name && !candidate.closest('[hidden]'),
  )
  if (!button) throw new Error(`ボタンが見つかりません: ${name}`)
  await act(() => button.click())
}

describe('カタログ検索', () => {
  it('自然言語と選択した条件を検索 API に送り、結果から詳細を開ける', async () => {
    const { calls } = await mount(found(agent), jsonResponse(200, detailOf(agent)))

    await changeSelect('type', 'AGENT')
    await changeSelect('a2a', 'true')
    await changeSelect('dataClassification', 'private')
    await search('受注を処理したい')

    expect(calls.map(({ url }) => url)).toEqual(['/api/catalog?type=AGENT', '/api/catalog/search'])
    expect(calls[1].init).toMatchObject({
      method: 'POST',
      headers: { authorization: 'Bearer viewer-token', 'content-type': 'application/json' },
    })
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      query: '受注を処理したい', type: 'AGENT', a2a: true, dataClassification: 'private',
    })
    expect(element('[aria-label="検索結果"]').textContent).toContain('受注エージェント')

    await clickButton('受注エージェントの詳細')
    expect(calls.at(-1)?.url).toBe('/api/catalog/agentAbc1234')
    const detail = element('[aria-labelledby="catalog-detail-heading"]')
    for (const text of [
      '定義', 'a2aAgentCard', '所有チーム', 'team-orders', '連絡先', 'orders@example.com',
      '利用条件', '開発環境のみ', '接続先の参照', 'agent-endpoint/orders', '関連参照',
      'skillAbc1234', '2.3.4', '公開承認は実行許可ではありません',
    ]) expect(detail.textContent).toContain(text)

    await clickButton('カタログに戻る')
    expect(element('[aria-label="検索結果"]').textContent).toContain('受注エージェント')
  })

  it('データ分類で絞り込んだ結果を表示し、Agent 以外では A2A の条件を送らない', async () => {
    const { calls } = await mount(found(mcp))

    await changeSelect('a2a', 'true')
    await changeSelect('type', 'MCP')
    expect(element<HTMLSelectElement>('select[name="a2a"]').disabled).toBe(true)
    await changeSelect('dataClassification', 'public')
    await search('在庫')

    expect(JSON.parse(String(calls.at(-1)?.init?.body))).toEqual({
      query: '在庫', type: 'MCP', dataClassification: 'public',
    })
    const results = element('[aria-label="検索結果"]')
    expect(results.textContent).toContain('在庫 MCP')
    expect(results.textContent).not.toContain('受注エージェント')
  })

  it('検索語が空白だけなら API を呼ばない', async () => {
    const { calls } = await mount()

    await changeSelect('dataClassification', 'private')
    await search('   ')

    expect(calls.map(({ url }) => url)).toEqual(['/api/catalog?type=AGENT'])
    expect(container.querySelector('[aria-label="検索結果"]')).toBeNull()
  })

  it('0件・認証エラー・Registry の障害・接続エラーを別の状態として表示する', async () => {
    const { relogin } = await mount(
      found(),
      jsonResponse(401, { message: 'Unauthorized' }),
      errorResponse(502, 'DISCOVERY_UNAVAILABLE', 'カタログを取得できませんでした。時間をおいて再度お試しください。'),
      new Error('通信失敗'),
      found(agent),
    )

    await search('なし')
    const results = element('[aria-label="検索結果"]')
    expect(results.textContent).toContain('条件に一致するカタログ項目はありません。')
    expect(results.querySelector('[role="alert"]')).toBeNull()

    await search('受注')
    expect(results.querySelector('[role="alert"]')?.textContent).toContain('認証が必要です')
    expect(results.textContent).not.toContain('条件に一致するカタログ項目はありません。')
    expect(buttonNames(results)).not.toContain('再試行')
    await clickButton('もう一度ログインする')
    expect(relogin).toHaveBeenCalledOnce()

    await search('受注')
    expect(results.querySelector('[role="alert"]')?.textContent).toContain('カタログを取得できませんでした')
    expect(results.textContent).not.toContain('条件に一致するカタログ項目はありません。')
    expect(buttonNames(results)).toContain('再試行')
    expect(buttonNames(results)).not.toContain('もう一度ログインする')

    await search('受注')
    expect(results.querySelector('[role="alert"]')?.textContent).toContain('API に接続できません')
    expect(buttonNames(results)).not.toContain('もう一度ログインする')

    await clickButton('再試行')
    expect(results.textContent).toContain('受注エージェント')
  })

  it('A2A 検索の準備前は0件ではなく準備中と示し、A2A の一覧へ案内する', async () => {
    const { calls } = await mount(
      errorResponse(503, 'A2A_SEARCH_NOT_READY', 'A2A での絞り込み検索は準備中です。'),
      jsonResponse(200, { items: [agent], nextToken: 'a2a-token' }),
      jsonResponse(200, { items: [], nextToken: null }),
      jsonResponse(200, { items: [agent], nextToken: null }),
    )

    await changeSelect('a2a', 'true')
    await search('受注')

    const results = element('[aria-label="検索結果"]')
    expect(results.querySelector('[role="alert"]')?.textContent).toContain('準備中です')
    expect(results.textContent).not.toContain('条件に一致するカタログ項目はありません。')
    expect(buttonNames(results)).not.toContain('再試行')
    await clickButton('A2A の Agent 一覧を開く')
    expect(calls.at(-1)?.url).toBe('/api/catalog?type=AGENT&a2a=true')
    expect(element<HTMLInputElement>('input[name="a2aOnly"]').checked).toBe(true)
    await clickButton('次のページ')
    expect(calls.at(-1)?.url).toBe('/api/catalog?type=AGENT&a2a=true&nextToken=a2a-token')
    expect(element('[aria-label="ページ"]').textContent).toContain('ページ 2')

    await act(() => element<HTMLInputElement>('input[name="a2aOnly"]').click())
    expect(calls.at(-1)?.url).toBe('/api/catalog?type=AGENT')
    expect(element('[aria-label="ページ"]').textContent).toContain('ページ 1')
  })

  it('A2A の検索用情報と定義が食い違って除外した件数を、結果とあわせて警告する', async () => {
    await mount(jsonResponse(200, { items: [agent], inconsistentA2ACount: 2 }))

    await changeSelect('a2a', 'true')
    await search('受注')

    const results = element('[aria-label="検索結果"]')
    expect(results.querySelector('[role="note"]')?.textContent).toContain('一致しない 2 件を結果から除外しました')
    expect(results.textContent).toContain('受注エージェント')
  })
})

describe('カタログ詳細', () => {
  it('公開版スナップショットの値がないものは未登録、取得できなかったものは未取得と表示する', async () => {
    await mount(
      jsonResponse(
        200,
        detailOf(agent, {
          contact: '',
          termsOfUse: null,
          connectionReference: null,
          dataClassification: null,
          relatedReferences: null,
        }),
      ),
    )

    await clickButton('受注エージェントの詳細')

    const terms = [...container.querySelectorAll('dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling?.textContent,
    ])
    expect(terms).toEqual(
      expect.arrayContaining([
        ['連絡先', '未登録'],
        ['利用条件', '未取得'],
        ['接続先の参照', '未取得'],
        ['データ分類', '未登録'],
      ]),
    )
    expect(element('[aria-labelledby="catalog-detail-heading"]').textContent).toContain('未取得')
  })

  it('公開版が見つからない場合と取得に失敗した場合をエラーとして表示し、再試行できる', async () => {
    await mount(
      errorResponse(404, 'CATALOG_ITEM_NOT_FOUND', '公開承認済みのカタログ項目が見つかりませんでした。'),
      errorResponse(502, 'DISCOVERY_UNAVAILABLE', 'カタログを取得できませんでした。時間をおいて再度お試しください。'),
      jsonResponse(200, detailOf(agent)),
    )

    await clickButton('受注エージェントの詳細')
    const detail = element('[aria-labelledby="catalog-detail-heading"]')
    expect(detail.querySelector('[role="alert"]')?.textContent).toContain('見つかりませんでした')
    expect(detail.textContent).not.toContain('公開承認は実行許可ではありません')

    await clickButton('再試行')
    expect(detail.querySelector('[role="alert"]')?.textContent).toContain('カタログを取得できませんでした')
    expect(buttonNames(detail)).not.toContain('もう一度ログインする')

    await clickButton('再試行')
    expect(detail.querySelector('[role="alert"]')).toBeNull()
    expect(detail.textContent).toContain('team-orders')
  })

  it('一覧から開いた SKILL.md の HTML・スクリプトを文字列として表示する', async () => {
    const skillMarkdown = '# 手順\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[リンク](javascript:alert(1))'
    const { calls } = await mount(
      jsonResponse(200, { items: [skill], nextToken: null }),
      jsonResponse(
        200,
        detailOf(skill, {
          definition: { agentSkillsDefinition: { dataSchemaVersion: '0.1.0' } },
          skillMarkdown,
          relatedReferences: [],
        }),
      ),
    )

    await clickButton('Skill')
    await clickButton('受注スキルの詳細')

    expect(calls.at(-1)?.url).toBe('/api/catalog/skillAbc1234')
    const markdown = element('[aria-label="SKILL.md"]')
    expect(markdown.textContent).toBe(skillMarkdown)
    expect(markdown.querySelector('script,img,a')).toBeNull()
    expect(markdown.innerHTML).toContain('&lt;script&gt;')
  })
})
