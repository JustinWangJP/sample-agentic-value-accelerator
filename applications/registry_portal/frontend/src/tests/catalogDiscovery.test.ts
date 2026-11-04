// @vitest-environment happy-dom

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, type CatalogItem } from '../api/client'
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

describe('カタログ検索と詳細画面', () => {
  it('自然言語と選択した条件を検索 API に送り、結果から詳細を開ける', async () => {
    const { calls } = await mount(
      jsonResponse(200, { items: [agent] }),
      jsonResponse(200, {
        ...agent,
        definition: { a2aAgentCard: { data: '{"name":"orders-agent"}' } },
        ownerTeam: 'team-orders',
        contact: 'orders@example.com',
        termsOfUse: '開発環境のみ',
        connectionReference: 'agent-endpoint/orders',
        dataClassification: 'private',
        skillMarkdown: '',
        relatedReferences: [{ recordId: 'skillAbc1234', recordVersion: '2.3.4' }],
      }),
    )

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

  it('0件・認証エラー・接続エラーを別の状態として表示する', async () => {
    const { relogin } = await mount(
      jsonResponse(200, { items: [] }),
      jsonResponse(401, { message: 'Unauthorized' }),
      new Error('通信失敗'),
      jsonResponse(200, { items: [agent] }),
    )

    await search('なし')
    const results = element('[aria-label="検索結果"]')
    expect(results.textContent).toContain('条件に一致するカタログ項目はありません。')
    expect(results.querySelector('[role="alert"]')).toBeNull()

    await search('受注')
    expect(results.querySelector('[role="alert"]')?.textContent).toContain('認証が必要です')
    expect(results.textContent).not.toContain('条件に一致するカタログ項目はありません。')
    await clickButton('もう一度ログインする')
    expect(relogin).toHaveBeenCalledOnce()

    await search('受注')
    expect(results.querySelector('[role="alert"]')?.textContent).toContain('API に接続できません')
    expect(results.textContent).not.toContain('もう一度ログインする')

    await clickButton('再試行')
    expect(results.textContent).toContain('受注エージェント')
  })

  it('一覧から開いた SKILL.md の HTML・スクリプトを文字列として表示する', async () => {
    const skillMarkdown = '# 手順\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[リンク](javascript:alert(1))'
    const { calls } = await mount(
      jsonResponse(200, { items: [skill], nextToken: null }),
      jsonResponse(200, {
        ...skill,
        definition: { agentSkillsDefinition: { dataSchemaVersion: '0.1.0' } },
        ownerTeam: '',
        contact: '',
        termsOfUse: '',
        connectionReference: '',
        dataClassification: '',
        skillMarkdown,
        relatedReferences: [],
      }),
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
