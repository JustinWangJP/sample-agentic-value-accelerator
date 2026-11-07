// @vitest-environment happy-dom
// A2A・MCP サーバー・Skill の下書き登録（Seam 2: SPA の fetch 境界）。
// 種別の選択、descriptor ごとの入力項目、JSON 入力、サーバーの日本語エラーの表示、送信する本文を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { AgentDraftsView } from '../drafts/AgentDraftsView'
import { fakeFetch, jsonResponse } from './helpers'

const common = {
  name: 'payments-a2a',
  displayName: '決済',
  description: '決済を調べる',
  recordVersion: '1.0.0',
  ownerTeam: 'team-payments',
  environment: 'dev',
  dataClassification: 'private',
  connectionReference: 'https://example.com/payments',
  contact: 'payments@example.com',
  termsOfUse: '社内利用のみ',
}
const card = '{"name":"payments-a2a","description":"決済","version":"1.0.0"}'
const a2aDraft = {
  ...common,
  recordType: 'AGENT',
  descriptorType: 'a2aAgentCard',
  schemaVersion: '0.3',
  definition: card,
}
const mcpDraft = {
  ...common,
  name: 'payments-mcp',
  recordType: 'MCP',
  descriptorType: 'mcpServer',
  schemaVersion: '2025-12-11',
  definition: '{"name":"io.example/payments","description":"d","version":"1.0.0"}',
  toolsSchemaVersion: '2025-11-25',
  toolsDefinition: '{"tools":[{"name":"t","inputSchema":{}}]}',
}
const skillDraft = {
  ...common,
  name: 'payments-skill',
  recordType: 'SKILL',
  descriptorType: 'agentSkillsDefinition',
  schemaVersion: '0.1.0',
  skillRepositoryUrl: 'https://github.com/example/skill',
  skillRepositorySource: 'github',
  skillCommit: '0123456789abcdef0123456789abcdef01234567',
  skillVersion: '1.2.0',
  skillDigest: '',
  skillMd: '---\nname: s\ndescription: d\n---\n',
}
const detailOf = (draft: object) => ({
  recordId: 'agentABC1234',
  draft,
  contentHash: 'a'.repeat(64),
  status: 'DRAFT',
  hasDraft: true,
  publishedVersion: null,
})
const succeeded = (draft: object) => ({
  ...detailOf(draft),
  operationId: 'op-1',
  operationState: 'SUCCEEDED',
})
const empty = jsonResponse(200, { items: [], nextToken: null })

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

type Field = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
function field(name: string): Field | null {
  return container.querySelector<Field>(`[name="${name}"]`)
}
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
  const node = field(name)
  if (!node) throw new Error(`項目がありません ${name}`)
  const prototype =
    node instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : node instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set
  await act(() => {
    setter?.call(node, value)
    node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}
async function fill(values: Record<string, unknown>) {
  for (const [name, value] of Object.entries(values)) {
    const node = field(name)
    if (!node || (node instanceof HTMLInputElement && node.readOnly) || node.disabled) continue
    if (node instanceof HTMLSelectElement && node.value === value) continue
    await setValue(name, String(value))
  }
}
async function submit() {
  await act(() =>
    container
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
}
async function mount(responses: Response[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  await act(() =>
    root.render(
      createElement(AgentDraftsView, {
        api,
        roles: ['Publisher'],
        teams: ['team-payments'],
        onRelogin: vi.fn(),
      }),
    ),
  )
  return calls
}
function sentBody(calls: { init?: RequestInit }[], index = 1) {
  return JSON.parse(String(calls[index]?.init?.body))
}

it('種別に MCP を選ぶと server 定義と tools 定義の入力が現れ、その本文で作成する', async () => {
  const calls = await mount([empty, jsonResponse(201, succeeded(mcpDraft))])
  await click('下書きを作成')
  expect(field('definition')).toBeNull()
  await setValue('recordType', 'MCP')
  expect(field('definition')).not.toBeNull()
  expect(field('toolsDefinition')).not.toBeNull()
  expect(field('purpose')).toBeNull()
  await fill(mcpDraft)
  await submit()
  expect(calls[1]?.url).toBe('/api/records')
  expect(sentBody(calls)).toEqual(mcpDraft)
  expect(container.textContent).toContain('下書きを保存しました。')
})

it('MCP の server と tools の schema version は別々の選択肢から選ぶ', async () => {
  await mount([empty])
  await click('下書きを作成')
  await setValue('recordType', 'MCP')
  const options = (name: string) =>
    [...(field(name) as HTMLSelectElement).options].map((option) => option.value)
  expect(options('schemaVersion')).toContain('2025-12-11')
  expect(options('schemaVersion')).not.toContain('2025-11-25')
  expect(options('toolsSchemaVersion')).toContain('2025-11-25')
  expect(options('toolsSchemaVersion')).not.toContain('2025-12-11')
})

it('A2A は Agent の descriptor の切り替えとして選び、Agent Card を送る', async () => {
  const calls = await mount([empty, jsonResponse(201, succeeded(a2aDraft))])
  await click('下書きを作成')
  expect(field('purpose')).not.toBeNull()
  await setValue('descriptorType', 'a2aAgentCard')
  expect(field('purpose')).toBeNull()
  expect((field('schemaVersion') as HTMLSelectElement).value).toBe('0.3')
  await fill(a2aDraft)
  await submit()
  expect(sentBody(calls)).toEqual(a2aDraft)
})

it('Skill はリポジトリ・固定した commit と version・digest・SKILL.md を送り、digest は空でもよい', async () => {
  const calls = await mount([empty, jsonResponse(201, succeeded(skillDraft))])
  await click('下書きを作成')
  await setValue('recordType', 'SKILL')
  expect(field('skillDigest')?.hasAttribute('required')).toBe(false)
  expect(field('skillCommit')?.hasAttribute('required')).toBe(true)
  await fill(skillDraft)
  await submit()
  expect(sentBody(calls)).toEqual(skillDraft)
})

it('environment は dev 固定で、データ分類の選択肢は public と private だけ', async () => {
  await mount([empty])
  await click('下書きを作成')
  const environment = field('environment') as HTMLInputElement
  expect(environment.value).toBe('dev')
  expect(environment.readOnly).toBe(true)
  const classes = [...(field('dataClassification') as HTMLSelectElement).options].map(
    (option) => option.value,
  )
  expect(classes).toEqual(['public', 'private'])
})

it('サーバーの日本語エラー（秘密情報・schema version）を保存の失敗として表示し、入力を残す', async () => {
  const message =
    '秘密情報らしき値が含まれているため保存できません: definition.token（資格情報らしき項目の値）。'
  const calls = await mount([
    empty,
    jsonResponse(400, { error: { code: 'VALIDATION_ERROR', message } }),
  ])
  await click('下書きを作成')
  await setValue('descriptorType', 'a2aAgentCard')
  await fill(a2aDraft)
  await submit()
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(message)
  expect(field('definition')?.value).toBe(card)
  expect(calls).toHaveLength(2)
})

it('JSON で入力すると、種別と descriptor の項目に反映して送る', async () => {
  const calls = await mount([empty, jsonResponse(201, succeeded(skillDraft))])
  await click('下書きを作成')
  await click('JSON で入力')
  await setValue('draftJson', JSON.stringify(skillDraft))
  expect(container.querySelector('[role="alert"]')).toBeNull()
  await submit()
  expect(sentBody(calls)).toEqual(skillDraft)
  await click('フォームで入力')
})

it('JSON が読めない・種別と descriptor の組合せが不正なときは日本語で示し、送信しない', async () => {
  const calls = await mount([empty])
  await click('下書きを作成')
  await click('JSON で入力')
  await setValue('draftJson', '{not json')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    'JSON として読み取れません。',
  )
  expect(button('下書きを保存').disabled).toBe(true)
  await setValue('draftJson', JSON.stringify({ ...mcpDraft, descriptorType: 'a2aAgentCard' }))
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    '種別と descriptor の組合せが正しくありません。',
  )
  await setValue('draftJson', JSON.stringify({ ...mcpDraft, definition: 1 }))
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    'definition は文字列で指定してください。',
  )
  await submit()
  expect(calls).toHaveLength(1)
})

it('JSON の environment・データ分類の値はサーバーが検証し、その日本語エラーを表示する', async () => {
  const message = 'データ分類は public または private を指定してください。'
  const calls = await mount([
    empty,
    jsonResponse(400, { error: { code: 'VALIDATION_ERROR', message } }),
  ])
  await click('下書きを作成')
  await click('JSON で入力')
  await setValue('draftJson', JSON.stringify({ ...mcpDraft, dataClassification: 'confidential' }))
  expect(container.querySelector('[role="alert"]')).toBeNull()
  await submit()
  expect(sentBody(calls).dataClassification).toBe('confidential')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(message)
})

it('編集では種別を変えられず、Agent だけ descriptor を切り替えられる', async () => {
  const item = (draft: { name: string; recordType: string }) => ({
    recordId: 'agentABC1234',
    recordType: draft.recordType,
    name: draft.name,
    displayName: '決済',
    recordVersion: '1.0.0',
    ownerTeam: 'team-payments',
    status: 'DRAFT',
    hasDraft: true,
    publishedVersion: null,
    operationId: null,
  })
  await mount([
    jsonResponse(200, { items: [item(mcpDraft)], nextToken: null }),
    jsonResponse(200, detailOf(mcpDraft)),
  ])
  expect(container.textContent).toContain('MCP サーバー')
  await click('編集')
  expect((field('recordType') as HTMLSelectElement).disabled).toBe(true)
  expect((field('descriptorType') as HTMLSelectElement).disabled).toBe(true)
  await click('一覧に戻る')
})

it('Agent の編集では descriptor を A2A に切り替えて PATCH できる', async () => {
  const custom = {
    ...common,
    recordType: 'AGENT',
    descriptorType: 'custom',
    purpose: '目的',
    authentication: 'Cognito JWT',
  }
  const calls = await mount([
    jsonResponse(200, {
      items: [
        {
          recordId: 'agentABC1234',
          recordType: 'AGENT',
          name: custom.name,
          displayName: '決済',
          recordVersion: '1.0.0',
          ownerTeam: 'team-payments',
          status: 'DRAFT',
          hasDraft: true,
          publishedVersion: null,
          operationId: null,
        },
      ],
      nextToken: null,
    }),
    jsonResponse(200, detailOf(custom)),
    jsonResponse(200, succeeded(a2aDraft)),
  ])
  await click('編集')
  expect((field('recordType') as HTMLSelectElement).disabled).toBe(true)
  await setValue('descriptorType', 'a2aAgentCard')
  await setValue('definition', card)
  await submit()
  expect(calls[2]?.init?.method).toBe('PATCH')
  expect(sentBody(calls, 2)).toEqual({ draft: a2aDraft, expectedContentHash: 'a'.repeat(64) })
})
