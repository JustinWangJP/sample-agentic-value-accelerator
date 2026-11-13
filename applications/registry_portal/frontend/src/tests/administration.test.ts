// @vitest-environment happy-dom
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { AdministrationView } from '../administration/AdministrationView'
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
const connection = {
  registryArn: 'arn:aws:agent-registry:us-east-1:123456789012:registry/abcdEFGH1234',
  region: 'us-east-1', authorization: 'CUSTOM_JWT',
  connection: { state: 'CONNECTED' }, autoApproval: { state: 'DISABLED' }, warnings: [],
}
const record = {
  recordId: 'agentABC1234', name: 'existing-agent', displayName: '既存 Agent', recordType: 'AGENT',
  recordVersion: '1.0.0', status: 'DRAFT', managed: false, ownerTeam: null, managementHash: null,
}
async function mount(responses: (Response | Error)[], roles: string[] = ['Admin']) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  await act(() => root.render(createElement(AdministrationView, { api, roles, onRelogin: vi.fn() })))
  return calls
}
async function click(label: string) {
  const button = [...container.querySelectorAll('button')].find((node) => node.textContent === label)
  if (!button) throw new Error(`ボタンがありません ${label}`)
  await act(() => button.click())
}
async function input(name: string, value: string) {
  const node = container.querySelector<HTMLInputElement>(`[name="${name}"]`)
  if (!node) throw new Error(`入力欄がありません ${name}`)
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

it('Admin 以外には管理画面を表示せず API も呼ばない', async () => {
  const calls = await mount([], ['Curator', 'Publisher'])
  expect(calls).toHaveLength(0)
  expect(container.textContent).not.toContain('Registry 管理')
})

it('接続情報と未管理0件を区別して表示する', async () => {
  const calls = await mount([jsonResponse(200, connection), jsonResponse(200, { items: [], nextToken: null })])
  expect(calls.map((call) => call.url)).toEqual(['/api/admin/connection', '/api/admin/records?management=unmanaged'])
  expect(container.textContent).toContain(connection.registryArn)
  expect(container.textContent).toContain('CUSTOM_JWT')
  expect(container.textContent).toContain('未管理レコードはありません。')
})

it('チームと理由を付けて取り込み、A2A の通常公開手順を案内する', async () => {
  const calls = await mount([
    jsonResponse(200, connection), jsonResponse(200, { items: [record], nextToken: null }),
    jsonResponse(200, { recordId: record.recordId, ownerTeam: 'team-payments', operationId: 'op-1', operationState: 'SUCCEEDED', publishedVersion: '1.0.0', a2aBackfillRequired: true }),
  ])
  await click('既存 Agent を選択')
  await input('adminOwnerTeam', 'team-payments')
  await input('adminReason', '所有チームを確認した')
  await click('取り込む')
  expect(calls[2]?.url).toBe('/api/records/agentABC1234/adopt')
  expect(JSON.parse(String(calls[2]?.init?.body))).toEqual({ ownerTeam: 'team-payments', reason: '所有チームを確認した' })
  expect(container.textContent).toContain('取り込みが完了しました')
  expect(container.textContent).toContain('通常の編集・申請・公開承認')
})

it('所有変更は取得時の管理情報ハッシュを送り、409 で再読み込みを案内する', async () => {
  const calls = await mount([
    jsonResponse(200, connection), jsonResponse(200, { items: [], nextToken: null }),
    jsonResponse(200, { items: [{ ...record, managed: true, ownerTeam: 'team-payments', managementHash: 'a'.repeat(64) }], nextToken: null }),
    jsonResponse(409, { error: { code: 'ADMINISTRATION_CONFLICT', message: '管理情報が変わりました。' } }),
    jsonResponse(200, { items: [], nextToken: null }),
  ])
  await click('管理済みレコード')
  await click('既存 Agent を選択')
  await input('adminOwnerTeam', 'team-finance')
  await input('adminReason', '組織変更')
  await click('所有チームを変更する')
  expect(calls[3]?.url).toBe('/api/records/agentABC1234/reassign-owner')
  expect(JSON.parse(String(calls[3]?.init?.body))).toEqual({ ownerTeam: 'team-finance', reason: '組織変更', expectedManagementHash: 'a'.repeat(64) })
  expect(container.textContent).toContain('一覧を再読み込みして最新の管理情報')
  expect(container.textContent).not.toContain('変更が完了しました')
  await click('一覧を再読み込み')
  expect(calls[4]?.url).toBe('/api/admin/records?management=managed')
})

it('自動承認の逸脱と接続エラーを Admin に表示する', async () => {
  await mount([
    jsonResponse(200, { ...connection, authorization: null, connection: { state: 'ERROR', code: 'REGISTRY_CONNECTION_ERROR', message: '接続情報を取得できません。' }, autoApproval: { state: 'UNKNOWN' }, warnings: ['自動承認の設定は未取得です。'] }),
    jsonResponse(502, { error: { code: 'REGISTRY_UNAVAILABLE', message: 'Registry を取得できません。' } }),
  ])
  expect(container.textContent).toContain('接続情報を取得できません。')
  expect(container.textContent).toContain('自動承認の設定は未取得です。')
  expect(container.textContent).not.toContain('未管理レコードはありません。')
})

it('空のページに nextToken があれば続きを取得し、異常応答を0件と表示しない', async () => {
  const calls = await mount([
    jsonResponse(200, { ...connection, autoApproval: { state: 'ENABLED' }, warnings: ['自動承認の設定が想定から外れています。'] }),
    jsonResponse(200, { items: [], nextToken: 'next/value' }),
    jsonResponse(200, { items: '不正', nextToken: null }),
  ])
  expect(container.textContent).toContain('自動承認の設定が想定から外れています。')
  expect(container.textContent).toContain('次のページも確認してください。')
  await click('次のページ')
  expect(calls[2]?.url).toBe('/api/admin/records?management=unmanaged&nextToken=next%2Fvalue')
  expect(container.textContent).toContain('API から想定外の応答')
  expect(container.textContent).not.toContain('未管理レコードはありません。')
})

it('通信結果が不明な取り込みは同じキーで再試行できる', async () => {
  const calls = await mount([
    jsonResponse(200, connection), jsonResponse(200, { items: [record], nextToken: null }),
    new Error('通信失敗'),
    jsonResponse(200, { recordId: record.recordId, ownerTeam: 'team-payments', operationId: 'op-1', operationState: 'SUCCEEDED', publishedVersion: null, a2aBackfillRequired: false }),
  ])
  await click('既存 Agent を選択')
  await input('adminOwnerTeam', 'team-payments')
  await input('adminReason', '管理を開始する')
  await click('取り込む')
  expect(container.textContent).not.toContain('取り込みが完了しました')
  await click('取り込む')
  expect(calls[3]?.init?.headers).toEqual(calls[2]?.init?.headers)
  expect(container.textContent).toContain('取り込みが完了しました')
})
