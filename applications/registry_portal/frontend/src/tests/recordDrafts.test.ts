// @vitest-environment happy-dom
// A2A・MCP サーバー・Skill の下書き登録（Seam 2: SPA の fetch 境界）。
// 種別の選択、descriptor ごとの入力項目、JSON 入力、サーバーの日本語エラーの表示、送信する本文を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { RecordDraftsView } from '../drafts/RecordDraftsView'
import { settlesIdempotencyKey } from '../drafts/api'
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
  toolsDefinition: '{"tools":[{"name":"t","inputSchema":{"type":"object"}}]}',
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
async function mount(responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  await act(() =>
    root.render(
      createElement(RecordDraftsView, {
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
function idempotencyKey(calls: { init?: RequestInit }[], index: number) {
  return new Headers(calls[index]?.init?.headers).get('idempotency-key')
}

function savedList(draft: { name: string; recordType: string } = a2aDraft, status = 'DRAFT') {
  return jsonResponse(200, {
    items: [{
      ...detailOf(draft), status, ...draft, operationId: null,
    }],
    nextToken: null,
  })
}

it('保存済み Agent を理由と関連参照で申請し、審査待ちを表示する', async () => {
  const calls = await mount([
    savedList(),
    jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(200, { ...succeeded(a2aDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '決済チームで利用するため')
  await click('関連参照を追加')
  await setValue('referenceId-0', 'mcpABC123456')
  await setValue('referenceVersion-0', '1.2.0')
  await submit()
  expect(calls[2]?.url).toBe('/api/records/agentABC1234/submit')
  expect(calls[2]?.init?.method).toBe('POST')
  expect(sentBody(calls, 2)).toEqual({
    expectedContentHash: 'a'.repeat(64),
    reason: '決済チームで利用するため',
    relatedReferences: [{ recordId: 'mcpABC123456', recordVersion: '1.2.0' }],
  })
  expect(container.textContent).toContain('申請しました。審査待ちです。')
})

it('保存の結果確認中は、変更がなくても申請を停止する', async () => {
  await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(202, { operationId: 'op-save', operationState: 'VERIFYING', message: '結果確認中' }),
  ])
  await click('編集')
  await submit()
  expect(button('保存済みの内容を申請').disabled).toBe(true)
})

it('MCP の申請には関連参照を表示せず、空の参照を送る', async () => {
  const calls = await mount([
    savedList(mcpDraft), jsonResponse(200, detailOf(mcpDraft)),
    jsonResponse(200, { ...succeeded(mcpDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  expect(field('referenceId-0')).toBeNull()
  expect(container.textContent).not.toContain('関連参照を追加')
  await setValue('reason', 'MCP の利用を承認してください')
  await submit()
  expect(sentBody(calls, 2).relatedReferences).toEqual([])
})

it('申請の検証エラーは理由と参照を残し、訂正して再申請できる', async () => {
  const calls = await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(400, { error: { code: 'VALIDATION_ERROR', message: '関連参照はカタログ項目ではありません。' } }),
    jsonResponse(200, { ...succeeded(a2aDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await click('関連参照を追加')
  await setValue('referenceId-0', 'mcpABC123456')
  await setValue('referenceVersion-0', '1.0.0')
  await submit()
  expect(container.textContent).toContain('関連参照はカタログ項目ではありません。')
  expect(field('reason')?.value).toBe('確認をお願いします')
  expect(field('referenceId-0')?.value).toBe('mcpABC123456')
  await click('関連参照 1 を削除')
  await submit()
  expect(calls).toHaveLength(4)
  // 検証エラーで失敗が確定したので、訂正した申請は新しい Idempotency-Key で送る
  expect(idempotencyKey(calls, 3)).not.toBe(idempotencyKey(calls, 2))
  expect(container.textContent).toContain('申請しました。審査待ちです。')
})

it('申請はフォームごとの Idempotency-Key を送る', async () => {
  const calls = await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(200, { ...succeeded(a2aDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await submit()
  expect(idempotencyKey(calls, 2)).toMatch(/^[A-Za-z0-9_-]{1,128}$/)
})

it('申請の内容の競合（409）は、一覧に戻って最新の内容を取得するよう案内する', async () => {
  await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(409, {
      error: { code: 'CONTENT_CONFLICT', message: '内容が変更されたか、別の操作が処理中です。' },
    }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await submit()
  expect(container.textContent).toContain('内容が変更されたか、別の操作が処理中です。')
  expect(container.textContent).toContain('一覧に戻って最新の内容を取得し、確認してから申請してください。')
  expect(container.textContent).not.toContain('申請結果が不明です。')
})

it('自動承認の設定変更（503）は結果不明と扱わず、理由を示して再申請できる状態に戻す', async () => {
  const calls = await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(503, {
      error: {
        code: 'APPROVAL_CONFIGURATION_CHANGED',
        message: '自動承認の設定が変更されたため申請を停止しました。管理者に連絡してください。',
      },
    }),
    jsonResponse(200, { ...succeeded(a2aDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await submit()
  expect(container.textContent).toContain('自動承認の設定が変更されたため申請を停止しました。')
  expect(container.textContent).not.toContain('申請結果が不明です。')
  expect(button('申請する').closest('fieldset')?.disabled).toBe(false)
  await submit()
  expect(calls).toHaveLength(4)
  expect(idempotencyKey(calls, 3)).not.toBe(idempotencyKey(calls, 2))
  expect(container.textContent).toContain('申請しました。審査待ちです。')
})

it('申請の結果確認中は再送せず、操作の照合で審査待ちになる', async () => {
  const calls = await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(202, { operationId: 'op-submit', operationState: 'VERIFYING', message: '照合中' }),
    jsonResponse(200, { ...succeeded(a2aDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await submit()
  expect(button('申請する').closest('fieldset')?.disabled).toBe(true)
  await submit()
  expect(calls).toHaveLength(3)
  await click('処理結果を確認')
  expect(calls[3]?.url).toBe('/api/operations/op-submit')
  expect(container.textContent).toContain('申請しました。審査待ちです。')
})

it('Admin の回復操作で確定した申請は、照会で回復結果を見せてから結果を表示する', async () => {
  await mount([
    savedList(), jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(202, { operationId: 'op-submit', operationState: 'VERIFYING', message: '照合中' }),
    jsonResponse(200, {
      operationId: 'op-submit',
      operationState: 'FAILED',
      message: 'Admin が結果を確認できないまま操作を強制解除しました（理由: Registry を確認した）。',
      recovery: {
        mode: 'RELEASED',
        by: { issuer: 'https://issuer', subject: 'admin-1' },
        at: '2026-11-09T10:00:00+00:00',
        reason: 'Registry を確認した',
      },
    }),
  ])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await submit()
  await click('処理結果を確認')
  expect(container.textContent).toContain('Admin の回復操作で結果が確定しました。')
  expect(container.textContent).toContain('確定できないまま強制解除')
  expect(container.textContent).toContain('回復した主体 admin-1')
  await click('結果を表示')
  expect(container.textContent).toContain('申請に失敗しました。Admin が結果を確認できないまま操作を強制解除しました')
})

it('申請の通信断は成功とせず、再送を停止して一覧での確認を案内する', async () => {
  const calls = await mount([savedList(), jsonResponse(200, detailOf(a2aDraft)), new Error('通信断')])
  await click('編集')
  await click('保存済みの内容を申請')
  await setValue('reason', '確認をお願いします')
  await submit()
  expect(container.textContent).toContain('申請結果が不明です。')
  expect(container.textContent).not.toContain('申請しました。')
  await submit()
  expect(calls).toHaveLength(3)
})

it('審査待ちの編集は元申請の無効化を明示し、再保存後に申請できる', async () => {
  const calls = await mount([
    savedList(a2aDraft, 'PENDING_APPROVAL'),
    jsonResponse(200, { ...detailOf(a2aDraft), status: 'PENDING_APPROVAL' }),
    jsonResponse(200, { ...succeeded(a2aDraft), contentHash: 'b'.repeat(64) }),
    jsonResponse(200, { ...succeeded(a2aDraft), status: 'PENDING_APPROVAL' }),
  ])
  await click('編集')
  expect(container.textContent).toContain('元の申請は無効になります。')
  expect(button('保存済みの内容を申請').disabled).toBe(true)
  await submit()
  expect(container.textContent).toContain('元の申請を無効にしました。')
  await click('保存済みの内容を申請')
  await setValue('reason', '再申請します')
  await submit()
  expect(sentBody(calls, 3).expectedContentHash).toBe('b'.repeat(64))
})

it('未保存の変更を申請に混ぜず、保存を案内する', async () => {
  const calls = await mount([savedList(), jsonResponse(200, detailOf(a2aDraft))])
  await click('編集')
  await setValue('description', '変更した説明')
  expect(button('保存済みの内容を申請').disabled).toBe(true)
  await click('保存済みの内容を申請')
  expect(field('reason')).toBeNull()
  expect(calls).toHaveLength(2)
})

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
  expect(field('skillRepositoryUrl')?.hasAttribute('required')).toBe(true)
  await fill(skillDraft)
  await submit()
  expect(sentBody(calls)).toEqual(skillDraft)
})

it('Skill の固定情報は commit と version の一方だけでも送れ、両方が空ならサーバーの日本語エラーを表示する', async () => {
  const versionOnly = { ...skillDraft, skillCommit: '' }
  const message = '固定した commit または version の少なくとも一方を指定してください。'
  const calls = await mount([
    empty,
    jsonResponse(201, succeeded(versionOnly)),
    empty,
    jsonResponse(400, { error: { code: 'VALIDATION_ERROR', message } }),
  ])
  await click('下書きを作成')
  await setValue('recordType', 'SKILL')
  expect(field('skillCommit')?.hasAttribute('required')).toBe(false)
  expect(field('skillVersion')?.hasAttribute('required')).toBe(false)
  await fill(versionOnly)
  await submit()
  expect(sentBody(calls)).toEqual(versionOnly)
  await click('一覧に戻る')
  await click('下書きを作成')
  await setValue('recordType', 'SKILL')
  await fill({ ...skillDraft, skillCommit: '', skillVersion: '' })
  await submit()
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(message)
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

it('JSON の environment・データ分類は許可値の一覧で検証して日本語で示し、送信しない', async () => {
  const calls = await mount([empty])
  await click('下書きを作成')
  await click('JSON で入力')
  await setValue('draftJson', JSON.stringify({ ...mcpDraft, dataClassification: 'confidential' }))
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    'データ分類は public または private を指定してください。',
  )
  expect(button('下書きを保存').disabled).toBe(true)
  await setValue('draftJson', JSON.stringify({ ...mcpDraft, environment: 'prod' }))
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    'environment は dev のみ指定できます。',
  )
  await submit()
  expect(calls).toHaveLength(1)
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

it('作成と申請は、結果が不明な失敗だけ同じキーを保ち、確定した失敗では作り直す', () => {
  for (const status of [400, 401, 403, 409]) {
    expect(settlesIdempotencyKey({ status, code: 'ANY' })).toBe(true)
  }
  expect(settlesIdempotencyKey({ status: 429, code: 'REGISTRY_THROTTLED' })).toBe(true)
  expect(settlesIdempotencyKey({ status: 503, code: 'APPROVAL_CONFIGURATION_CHANGED' })).toBe(true)
  expect(settlesIdempotencyKey({ status: 503, code: 'DRAFTS_NOT_CONFIGURED' })).toBe(true)
  expect(settlesIdempotencyKey({ status: 0, code: 'NETWORK_ERROR' })).toBe(false)
  expect(settlesIdempotencyKey({ status: 502, code: 'REGISTRY_UNAVAILABLE' })).toBe(false)
})

it('廃止依頼は理由を本文に載せて送り、Curator への依頼を表示する', async () => {
  const calls = await mount([
    savedList(a2aDraft, 'APPROVED'),
    jsonResponse(200, detailOf(a2aDraft)),
    jsonResponse(200, {
      state: 'OPEN',
      requestId: 'request-1',
      recordId: 'agentABC1234',
      reason: '利用が終わったため',
      requestedBy: { issuer: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE', subject: 'alice' },
      requestedAt: '2026-11-09T01:00:00+00:00',
    }),
  ])
  await click('編集')
  await setValue('deprecationReason', '利用が終わったため')
  await act(() =>
    container
      .querySelectorAll('form')[1]
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
  expect(calls[2]?.url).toBe('/api/records/agentABC1234/deprecation-requests')
  expect(sentBody(calls, 2)).toEqual({ reason: '利用が終わったため' })
  expect(container.textContent).toContain('廃止依頼を送信しました。Curator が対応します')
})
