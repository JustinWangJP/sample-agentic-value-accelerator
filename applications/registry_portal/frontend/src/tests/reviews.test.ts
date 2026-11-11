// @vitest-environment happy-dom
// 審査と決裁（Seam 2: SPA の fetch 境界）。Curator の一覧・スナップショットの差分・
// 公開承認・却下の本文と結果の表示（検索への反映待ち、409 の再読み込み）と、
// 廃止依頼の項目（経過日数・公開停止済みの判断待ち・公開管理の入口）を確認する。
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
import { ReviewsView } from '../reviews/ReviewsView'
import { fakeFetch, jsonResponse } from './helpers'

const SNAPSHOT_HASH = 'b'.repeat(64)
const submittedBy = { issuer: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE', subject: 'publisher-1' }
const lastEditedBy = { ...submittedBy, subject: 'publisher-2' }
const summary = {
  reviewKind: 'SUBMISSION',
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '2.0.0',
  status: 'PENDING_APPROVAL',
  managed: true,
  ownerTeam: 'team-payments',
  submissionState: 'AVAILABLE',
  submissionId: 'submission-1',
  snapshotHash: SNAPSHOT_HASH,
  reason: '利用者向けに公開する',
  submittedAt: '2026-11-08T09:00:00+00:00',
  submittedBy,
  invalidated: false,
  operationId: null,
}
const snapshot = {
  submissionId: 'submission-1',
  submittedBy,
  lastEditedBy,
  submittedAt: '2026-11-08T09:00:00+00:00',
  reason: '利用者向けに公開する',
  record: {
    recordId: 'agentABC1234',
    name: 'payments-a2a',
    displayName: '決済 Agent',
    description: '決済を調べる',
    recordType: 'AGENT',
    recordVersion: '2.0.0',
    descriptors: { custom: { data: '{"purpose":"照会"}' } },
    customMetadata: { ownerTeam: 'team-payments', environment: 'dev', dataClassification: 'private' },
  },
  ownerTeam: 'team-payments',
  supplement: { ownerTeam: 'team-payments', contact: 'payments@example.com', termsOfUse: '社内利用のみ', connectionReference: 'https://example.com/payments' },
  relatedReferences: [],
  contact: 'new@example.com',
  termsOfUse: '改訂後の利用条件',
  connectionReference: 'https://example.com/payments-v2',
  contentHash: 'c'.repeat(64),
  snapshotHash: SNAPSHOT_HASH,
}
const detail = {
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  description: '決済を調べる',
  recordType: 'AGENT',
  recordVersion: '2.0.0',
  ownerTeam: 'team-payments',
  status: 'PENDING_APPROVAL',
  managed: true,
  publishedVersion: '1.0.0',
  submissionState: 'AVAILABLE',
  submissionId: 'submission-1',
  snapshotHash: SNAPSHOT_HASH,
  snapshotIntact: true,
  invalidated: false,
  snapshot,
  publishedState: 'AVAILABLE',
  published: {
    recordVersion: '1.0.0',
    contact: 'old@example.com',
    termsOfUse: '旧利用条件',
    connectionReference: 'https://example.com/payments',
    relatedReferences: [],
    record: { ...snapshot.record, description: '残高を調べる', recordVersion: '1.0.0' },
    approvedBy: { ...submittedBy, subject: 'curator-1' },
  },
  deprecationRequest: null,
}
const approveResult = {
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '2.0.0',
  status: 'APPROVED',
  ownerTeam: 'team-payments',
  publishedVersion: '2.0.0',
  operationId: 'decision-1',
  operationState: 'SUCCEEDED',
  submissionId: 'submission-1',
  snapshotHash: SNAPSHOT_HASH,
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
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function submitForm(index: number) {
  const form = container.querySelectorAll('form')[index]
  await act(() => form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}
async function mount(responses: (Response | Error)[], roles: string[] = ['Curator']) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const api = createApiClient({ baseUrl: '', fetch: fetchImpl, getAccessToken: () => 'token' })
  await act(() =>
    root.render(createElement(ReviewsView, { api, roles, onRelogin: vi.fn() })),
  )
  return calls
}
function sentBody(calls: { init?: RequestInit }[], index: number) {
  return JSON.parse(String(calls[index]?.init?.body))
}
function idempotencyKey(calls: { init?: RequestInit }[], index: number) {
  return new Headers(calls[index]?.init?.headers).get('idempotency-key')
}
/** 差分表の行（項目名）の「公開中の版」と「申請された版」 */
function diffRow(label: string): [string | null | undefined, string | null | undefined] {
  const row = [...container.querySelectorAll('table tr')].find(
    (element) => element.querySelector('th')?.textContent === label,
  )
  if (!row) throw new Error(`差分の行がありません ${label}`)
  const cells = row.querySelectorAll('td')
  return [cells[0]?.textContent, cells[1]?.textContent]
}

it('Curator 以外には審査待ちの画面を出さない', async () => {
  const calls = await mount([jsonResponse(200, { items: [], nextToken: null })], ['Publisher'])
  expect(container.textContent).not.toContain('審査待ち')
  expect(calls).toHaveLength(0)
})

it('AWS の承認待ちに申請の補助情報を結合して一覧に表示する', async () => {
  const calls = await mount([jsonResponse(200, { items: [summary], nextToken: null })])
  expect(calls[0]?.url).toBe('/api/reviews')
  expect(container.textContent).toContain('決済 Agent')
  expect(container.textContent).toContain('申請理由: 利用者向けに公開する（申請者 publisher-1）')
  // AWS の PENDING_APPROVAL は画面で「審査待ち」と表示する
  expect(container.textContent).toContain('状態 審査待ち')
  await click('審査する')
  expect(calls).toHaveLength(2)
  expect(calls[1]?.url).toBe('/api/reviews/agentABC1234')
})

it('ポータルで管理されていない審査待ちには決裁の案内を出す', async () => {
  await mount([
    jsonResponse(200, {
      items: [{ ...summary, managed: false, ownerTeam: null, submissionState: 'NONE', submissionId: null, snapshotHash: null, reason: null, submittedBy: null }],
      nextToken: null,
    }),
  ])
  expect(container.textContent).toContain('ポータルで管理されていないため、ここでは決裁できません。')
  expect(button('審査する').disabled).toBe(true)
})

it('スナップショットと公開中の版の差分を表示し、公開承認の理由を付けて公開承認する', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(200, approveResult),
  ])
  await click('審査する')
  expect(container.textContent).toContain('申請者: publisher-1 / 最終編集者: publisher-2')
  expect(container.textContent).toContain('連絡先: new@example.com / 利用条件: 改訂後の利用条件')
  expect(container.textContent).toContain('公開中の版 1.0.0')
  // 差分: 公開中の版と申請された版。補助情報だけでなく定義と metadata も比べる
  expect(diffRow('連絡先')).toEqual(['old@example.com', 'new@example.com'])
  expect(diffRow('説明')).toEqual(['残高を調べる', '決済を調べる'])
  expect(diffRow('定義（descriptors）')[1]).toContain('照会')
  expect(container.textContent).toContain('公開承認した Curator: curator-1')
  // 公開承認にも理由が必須
  expect(button('公開承認する').disabled).toBe(true)
  await setValue('approveReason', '内容を確認した')
  expect(button('公開承認する').disabled).toBe(false)
  await submitForm(0)
  const body = sentBody(calls, 2)
  expect(calls[2]?.url).toBe('/api/reviews/agentABC1234/approve')
  expect(body).toEqual({ snapshotHash: SNAPSHOT_HASH, reason: '内容を確認した' })
  expect(idempotencyKey(calls, 2)).toMatch(/^[0-9a-f-]{36}$/)
  // 公開承認のあとは「検索への反映待ち」を表示する
  expect(container.textContent).toContain('公開承認しました（版 2.0.0）')
  expect(container.textContent).toContain('検索への反映待ち')
})

it('却下は理由が必須で、理由を本文に載せて送る', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(200, { ...approveResult, status: 'REJECTED', publishedVersion: null }),
  ])
  await click('審査する')
  // 理由なしでは却下のボタンが押せない
  expect(button('却下する').disabled).toBe(true)
  await setValue('rejectReason', '  ')
  expect(button('却下する').disabled).toBe(true)
  await setValue('rejectReason', '説明が不足している')
  expect(button('却下する').disabled).toBe(false)
  await click('却下する')
  expect(calls[2]?.url).toBe('/api/reviews/agentABC1234/reject')
  expect(sentBody(calls, 2)).toEqual({ snapshotHash: SNAPSHOT_HASH, reason: '説明が不足している' })
  expect(container.textContent).toContain('却下しました')
  expect(container.textContent).not.toContain('検索への反映待ち')
})

it('409 のときは再読み込みを促し、最新の申請を取得し直す', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(409, { error: { code: 'CONTENT_CONFLICT', message: '審査対象が変更されたか、別の操作が処理中です。最新の内容を確認してください。' } }),
    jsonResponse(200, detail),
  ])
  await click('審査する')
  await setValue('approveReason', '承認')
  await submitForm(0)
  expect(container.textContent).toContain('審査した内容が現在の申請と一致しません')
  await click('最新の申請を再読み込み')
  expect(calls[3]?.url).toBe('/api/reviews/agentABC1234')
})

it('自己承認の403は案内を変えずに日本語メッセージを表示する', async () => {
  await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(403, { error: { code: 'FORBIDDEN', message: '申請者と最終編集者は決裁できません。別の Curator が審査してください。' } }),
  ])
  await click('審査する')
  await setValue('approveReason', '承認')
  await submitForm(0)
  expect(container.textContent).toContain('申請者と最終編集者は決裁できません')
  expect(container.textContent).not.toContain('もう一度ログイン')
})

it('結果確認中の決裁は操作照会で確定し、承認後に反映待ちを表示する', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(200, { operationId: 'decision-1', operationState: 'VERIFYING', message: '結果確認中です。' }),
    jsonResponse(200, approveResult),
  ])
  await click('審査する')
  await setValue('approveReason', '内容を確認した')
  await submitForm(0)
  expect(container.textContent).toContain('決裁の結果確認中')
  await click('決裁の結果を確認')
  expect(calls[3]?.url).toBe('/api/operations/decision-1')
  expect(container.textContent).toContain('公開承認しました（版 2.0.0）')
  expect(container.textContent).toContain('検索への反映待ち')
})

it('Admin の回復操作で確定した決裁は、照会で回復結果を見せてから結果を表示する', async () => {
  await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(200, { operationId: 'decision-1', operationState: 'VERIFYING', message: '結果確認中です。' }),
    jsonResponse(200, {
      ...approveResult,
      recovery: {
        mode: 'RECONCILED',
        by: { issuer: 'https://issuer', subject: 'admin-1' },
        at: '2026-11-09T10:00:00+00:00',
        reason: 'Registry を確認した',
      },
    }),
  ])
  await click('審査する')
  await setValue('approveReason', '内容を確認した')
  await submitForm(0)
  await click('決裁の結果を確認')
  expect(container.textContent).toContain('Admin の回復操作で結果が確定しました。')
  expect(container.textContent).toContain('Registry を再取得して確定')
  expect(container.textContent).not.toContain('公開承認しました')
  await click('結果を表示')
  expect(container.textContent).toContain('公開承認しました（版 2.0.0）')
})

it('結果が不明な失敗のあとは同じキーで再試行する', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, detail),
    jsonResponse(502, { error: { code: 'REGISTRY_UNAVAILABLE', message: 'Registry を取得できません。' } }),
    jsonResponse(200, approveResult),
  ])
  await click('審査する')
  await setValue('approveReason', '内容を確認した')
  await submitForm(0)
  const firstKey = idempotencyKey(calls, 2)
  expect(container.textContent).toContain('決裁結果が不明です')
  await click('同じ内容で決裁を再試行')
  // 同じ Idempotency-Key で同じ本文を再送する
  expect(sentBody(calls, 3)).toEqual({ snapshotHash: SNAPSHOT_HASH, reason: '内容を確認した' })
  expect(idempotencyKey(calls, 3)).toBe(firstKey)
  expect(container.textContent).toContain('公開承認しました（版 2.0.0）')
})

it('申請はあるはずなのに読めない審査待ちは、「申請なし」と区別して表示する', async () => {
  await mount([
    jsonResponse(200, {
      items: [{ ...summary, submissionState: 'MISSING', snapshotHash: null, reason: null, submittedBy: null }],
      nextToken: null,
    }),
  ])
  expect(container.textContent).toContain('申請スナップショットを取得できません')
  expect(container.textContent).not.toContain('ポータル経由の申請なし')
  expect(button('審査する').disabled).toBe(true)
})

it('公開版スナップショットが読めないときは「初回公開」ではなく「未取得」と表示する', async () => {
  await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, { ...detail, publishedState: 'MISSING', published: null }),
  ])
  await click('審査する')
  expect(container.textContent).toContain('公開中の版 1.0.0 のスナップショットは未取得です')
  expect(container.textContent).not.toContain('初めて公開されます')
})

it('定義と承認者を記録する前の公開版は、差分を「未取得」、承認者を「不明」と表示する', async () => {
  await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, { ...detail, published: { ...detail.published, record: null, approvedBy: null } }),
  ])
  await click('審査する')
  expect(diffRow('説明')).toEqual(['未取得', '決済を調べる'])
  expect(container.textContent).toContain('公開承認した Curator: 不明')
})

it('書き換えられた申請スナップショットは決裁させない', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, { ...detail, snapshotIntact: false }),
  ])
  await click('審査する')
  expect(container.textContent).toContain('申請時の内容と一致しません')
  await setValue('approveReason', '内容を確認した')
  expect(button('公開承認する').disabled).toBe(true)
  await submitForm(0)
  expect(calls).toHaveLength(2)
})

it('状態が AWS の値の一覧にない応答は不正な応答として扱う', async () => {
  await mount([jsonResponse(200, { items: [{ ...summary, status: 'UNKNOWN' }], nextToken: null })])
  expect(container.textContent).not.toContain('決済 Agent')
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
})

/** 未解決の廃止依頼の項目。申請の項目（summary）とは形が異なる（バックエンドの ReviewQueue と揃える） */
const deprecationRequest = {
  reviewKind: 'DEPRECATION_REQUEST',
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '1.0.0',
  status: 'APPROVED',
  managed: true,
  ownerTeam: 'team-payments',
  requestId: 'request-1',
  reason: '利用が終わったため',
  requestedAt: '2026-11-01T01:00:00+00:00',
  requestedBy: { ...submittedBy, subject: 'publisher-1' },
  suspended: false,
  operationId: null,
}
const lifecycleView = {
  recordId: 'agentABC1234',
  name: 'payments-a2a',
  displayName: '決済 Agent',
  recordType: 'AGENT',
  recordVersion: '1.0.0',
  status: 'APPROVED',
  managed: true,
  ownerTeam: 'team-payments',
  publishedVersion: '1.0.0',
  suspended: false,
  operationId: null,
  deprecationRequest: {
    requestId: 'request-1',
    recordId: 'agentABC1234',
    reason: '利用が終わったため',
    requestedBy: { ...submittedBy, subject: 'publisher-1' },
    requestedAt: '2026-11-01T01:00:00+00:00',
    state: 'OPEN',
    resolution: null,
    resolutionReason: null,
    resolvedBy: null,
    resolvedAt: null,
  },
  actions: {
    suspend: { allowed: true, message: null },
    deprecate: { allowed: true, message: null },
  },
}

it('廃止依頼を申請と並べて一覧に表示し、経過日数と判断待ちの状況を確認できる', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-11T00:00:00+00:00'))
  await mount([
    jsonResponse(200, {
      items: [deprecationRequest, { ...deprecationRequest, recordId: 'mcpEFGH5678I', suspended: true }, summary],
      nextToken: null,
    }),
  ])
  vi.useRealTimers()
  expect(container.textContent).toContain('廃止依頼: 利用が終わったため（依頼者 publisher-1）')
  expect(container.textContent).toContain('未対応・9 日経過')
  expect(container.textContent).toContain('公開停止済み・廃止の判断待ち・9 日経過')
  // 申請の審査と廃止依頼への対応を同じ一覧で行う
  expect(container.textContent).toContain('申請理由: 利用者向けに公開する')
  expect(button('審査する').disabled).toBe(false)
})

it('廃止依頼の「対応する」で、同じレコードの公開管理を開く', async () => {
  const calls = await mount([
    jsonResponse(200, { items: [deprecationRequest], nextToken: null }),
    jsonResponse(200, lifecycleView),
  ])
  await click('対応する')
  expect(calls[1]?.url).toBe('/api/records/agentABC1234/lifecycle')
  expect(container.textContent).toContain('公開管理: 決済 Agent')
  expect(container.textContent).toContain('未解決の廃止依頼')
  expect(button('公開停止する')).toBeTruthy()
})

it('審査の詳細は、未解決の廃止依頼があれば決裁の前に警告する', async () => {
  await mount([
    jsonResponse(200, { items: [summary], nextToken: null }),
    jsonResponse(200, { ...detail, deprecationRequest: lifecycleView.deprecationRequest }),
  ])
  await click('審査する')
  expect(container.textContent).toContain('このレコードには未解決の廃止依頼があります（理由: 利用が終わったため')
})

it('一覧の項目は種別ごとの必須の項目を検査し、欠けていれば拒否する', async () => {
  const withoutKind: Record<string, unknown> = { ...summary }
  delete withoutKind.reviewKind
  const requestWithoutSuspended: Record<string, unknown> = { ...deprecationRequest }
  delete requestWithoutSuspended.suspended
  // 申請の項目の形で返った廃止依頼（以前の不整合）も拒否する
  const requestShapedAsSubmission = { ...summary, reviewKind: 'DEPRECATION_REQUEST' }
  for (const item of [withoutKind, requestWithoutSuspended, requestShapedAsSubmission]) {
    await mount([jsonResponse(200, { items: [item], nextToken: null })])
    expect(container.textContent).toContain('API から想定外の応答が返されました。')
    // 前の項目の表示を引き継がないよう、項目ごとに描画し直す
    await act(() => root.unmount())
    root = createRoot(container)
  }
})
