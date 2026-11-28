// @vitest-environment happy-dom
// 下書きの作成・編集画面の、未保存の変更の警告（Issue #85）。
// 画面遷移・「一覧に戻る」・戻る/進む・再読み込みの前に確認し、保存済みの内容と同じなら確認しない
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { RecordDraftsView } from '../drafts/RecordDraftsView'
import { memoryHashLocation, type MemoryHashLocation } from '../routing/location'
import { DISCARD_AND_LEAVE, KEEP_EDITING, UNSAVED_CHANGES_MESSAGE } from '../routing/NavigationProvider'
import { fakeFetch, jsonResponse, withNavigation, type FetchCall } from './helpers'

const draft = {
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
  recordType: 'AGENT',
  descriptorType: 'a2aAgentCard',
  schemaVersion: '0.3',
  definition: '{"name":"payments-a2a","description":"決済","version":"1.0.0"}',
}
const detail = (body: object = draft) => ({
  recordId: 'agentABC1234',
  draft: body,
  contentHash: 'a'.repeat(64),
  status: 'DRAFT',
  hasDraft: true,
  publishedVersion: null,
})
const list = () =>
  jsonResponse(200, {
    items: [{ ...detail(), ...draft, operationId: null }],
    nextToken: null,
  })

let container: HTMLDivElement
let root: Root
let location: MemoryHashLocation
let calls: FetchCall[]

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

async function mount(hash: string, responses: Response[]) {
  const fake = fakeFetch(responses)
  calls = fake.calls
  location = memoryHashLocation(hash)
  const api = createApiClient({ baseUrl: '', fetch: fake.fetchImpl, getAccessToken: () => 'token' })
  await act(() =>
    root.render(
      withNavigation(
        createElement(RecordDraftsView, { api, roles: ['Publisher'], teams: ['team-payments'], onRelogin: vi.fn() }),
        location,
      ),
    ),
  )
}

function button(label: string): HTMLButtonElement {
  const node = [...container.querySelectorAll('button')].find((element) => element.textContent === label)
  if (!node) throw new Error(`ボタンがありません ${label}`)
  return node
}
async function click(label: string) {
  await act(() => button(label).click())
}
async function setValue(name: string, value: string) {
  const node = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[name="${name}"]`)
  if (!node) throw new Error(`項目がありません ${name}`)
  const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(() => {
    Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function dialog(): Element | null {
  return container.querySelector('[role="dialog"]')
}
function heading(): string {
  return container.querySelector('h1')?.textContent ?? ''
}

describe('下書きの編集画面', () => {
  it('変更していなければ、一覧に戻るときに確認しない', async () => {
    await mount('#/drafts', [list(), jsonResponse(200, detail()), list()])
    await click('編集')
    expect(location.get()).toBe('#/drafts/agentABC1234')
    expect(heading()).toBe('下書きを編集')
    expect(location.attemptUnload()).toBe(false)

    await click('一覧に戻る')
    expect(dialog()).toBeNull()
    expect(location.get()).toBe('#/drafts')
  })

  it('変更があると「一覧に戻る」で確認し、「編集に戻る」なら入力を残したまま留まる', async () => {
    await mount('#/drafts', [list(), jsonResponse(200, detail()), list()])
    await click('編集')
    await setValue('displayName', '決済（改）')
    expect(container.textContent).toContain('未保存の変更あり')

    await click('一覧に戻る')
    expect(dialog()?.textContent).toContain(UNSAVED_CHANGES_MESSAGE)
    await click(KEEP_EDITING)
    expect(dialog()).toBeNull()
    expect(location.get()).toBe('#/drafts/agentABC1234')
    expect(container.querySelector<HTMLInputElement>('[name="displayName"]')?.value).toBe('決済（改）')

    await click('一覧に戻る')
    await click(DISCARD_AND_LEAVE)
    expect(location.get()).toBe('#/drafts')
    // 一覧を読み込み直す（保存していない変更は送らない）
    expect(calls.map((call) => call.init?.method ?? 'GET')).toEqual(['GET', 'GET', 'GET'])
  })

  it('変更があるあいだは、再読み込み・タブを閉じる前にブラウザの確認を出す', async () => {
    await mount('#/drafts/agentABC1234', [jsonResponse(200, detail())])
    expect(location.attemptUnload()).toBe(false)
    await setValue('description', '別の説明')
    expect(location.attemptUnload()).toBe(true)
    // 元の内容に戻せば、未保存の変更ではない
    await setValue('description', '決済を調べる')
    expect(location.attemptUnload()).toBe(false)
  })

  it('戻る操作で確認し、「編集に戻る」ならハッシュを編集画面に戻す', async () => {
    await mount('#/drafts', [list(), jsonResponse(200, detail())])
    await click('編集')
    await setValue('displayName', '決済（改）')
    await act(() => location.back())

    expect(dialog()).not.toBeNull()
    await click(KEEP_EDITING)
    expect(location.get()).toBe('#/drafts/agentABC1234')
    expect(heading()).toBe('下書きを編集')
  })

  it('保存に成功すると、未保存の変更はなくなり確認しない', async () => {
    const saved = { ...draft, displayName: '決済（改）' }
    await mount('#/drafts/agentABC1234', [
      jsonResponse(200, detail()),
      jsonResponse(200, { ...detail(saved), operationId: 'op-1', operationState: 'SUCCEEDED' }),
      list(),
    ])
    await setValue('displayName', '決済（改）')
    await act(() =>
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
    )
    expect(calls[1]?.init?.method).toBe('PATCH')
    expect(container.textContent).toContain('下書きを保存しました。')
    expect(location.attemptUnload()).toBe(false)

    await click('一覧に戻る')
    expect(dialog()).toBeNull()
    expect(location.get()).toBe('#/drafts')
  })

  it('JSON として読み取れない入力は、未保存の変更として扱う', async () => {
    await mount('#/drafts/agentABC1234', [jsonResponse(200, detail())])
    await click('JSON で入力')
    // JSON に切り替えただけでは変更ではない
    expect(location.attemptUnload()).toBe(false)
    await setValue('draftJson', '{ 壊れた JSON')
    expect(location.attemptUnload()).toBe(true)
  })

  it('URL から直接開いたときは、下書きを取得してから編集画面を出す', async () => {
    await mount('#/drafts/agentABC1234', [jsonResponse(200, detail())])
    expect(calls[0]?.url).toBe('/api/records/agentABC1234')
    expect(container.querySelector<HTMLInputElement>('[name="displayName"]')?.value).toBe('決済')
  })

  it('下書きを取得できなければ、日本語のエラーと一覧への戻り道を出す', async () => {
    await mount('#/drafts/agentABC1234', [
      jsonResponse(502, { error: { code: 'REGISTRY_UNAVAILABLE', message: '下書きを取得できませんでした。' } }),
    ])
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('下書きを取得できませんでした。')
    expect(button('一覧に戻る')).toBeDefined()
    expect(button('再試行')).toBeDefined()
  })
})

describe('下書きの作成画面', () => {
  it('初期値のままなら確認せず、入力したら確認する', async () => {
    await mount('#/drafts/new', [list()])
    expect(heading()).toBe('下書きを作成')
    expect(location.attemptUnload()).toBe(false)

    await setValue('name', 'new-agent')
    expect(location.attemptUnload()).toBe(true)
    await click('一覧に戻る')
    expect(dialog()).not.toBeNull()
    await click(DISCARD_AND_LEAVE)
    expect(location.get()).toBe('#/drafts')
  })
})
