import { useEffect, useRef, useState } from 'react'
import { toApiError, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import type {
  AdminPage,
  AdminRecord,
  AdministrationApi,
  AdministrationResult,
  AutoApprovalState,
  ManagementFilter,
  RegistryConnection,
} from './api'

type Props = { api: AdministrationApi; roles: readonly string[]; onRelogin: () => void }
export function AdministrationView(props: Props) {
  return props.roles.includes('Admin') ? <AdministrationPanel {...props} /> : null
}

const AUTO_APPROVAL_LABELS: Record<AutoApprovalState, string> = { DISABLED: '無効', ENABLED: '有効', UNKNOWN: '未取得' }

/** 選んだレコードに対する操作。取り込みと所有変更の違い（表示・本文・API）をここに集める */
type OwnershipAction = {
  title: string
  submitLabel: string
  doneLabel: string
  payload: Record<string, string>
  send: (key: string) => Promise<AdministrationResult>
}
function ownershipAction(api: AdministrationApi, record: AdminRecord, ownerTeam: string, reason: string): OwnershipAction {
  if (record.managed) {
    const payload = { ownerTeam, reason, expectedManagementHash: record.managementHash }
    return {
      title: '所有チーム変更', submitLabel: '所有チームを変更する', doneLabel: '所有チームの変更', payload,
      send: (key) => api.reassignOwner(record.recordId, payload, key),
    }
  }
  const payload = { ownerTeam, reason }
  return {
    title: '取り込み', submitLabel: '取り込む', doneLabel: '取り込み', payload,
    send: (key) => api.adoptRecord(record.recordId, payload, key),
  }
}

type ListQuery = { filter: ManagementFilter; token?: string; reload: number }
type ListState = { kind: 'loading' } | { kind: 'ready'; page: AdminPage } | { kind: 'error'; error: ApiError }

function AdministrationPanel({ api, onRelogin }: Props) {
  const [connection, setConnection] = useState<RegistryConnection | null>(null)
  const [connectionError, setConnectionError] = useState<ApiError | null>(null)
  const [query, setQuery] = useState<ListQuery>({ filter: 'unmanaged', reload: 0 })
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [error, setError] = useState<ApiError | null>(null)
  const [selected, setSelected] = useState<AdminRecord | null>(null)
  const [ownerTeam, setOwnerTeam] = useState('')
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const lastRequest = useRef<{ fingerprint: string; key: string } | null>(null)

  useEffect(() => {
    let active = true
    api.getRegistryConnection().then((result) => {
      if (active) setConnection(result)
    }).catch((failure: unknown) => {
      if (active) setConnectionError(toApiError(failure, '接続情報を取得できませんでした。'))
    })
    return () => { active = false }
  }, [api])

  // 一覧の取得はこの effect だけが行う。表示切替・再読み込み・次のページは query を変える
  useEffect(() => {
    let active = true
    api.listAdminRecords(query.filter, query.token).then((page) => {
      if (active) setList({ kind: 'ready', page })
    }).catch((failure: unknown) => {
      if (active) setList({ kind: 'error', error: toApiError(failure, '管理レコードを取得できませんでした。') })
    })
    return () => { active = false }
  }, [api, query])

  function show(filter: ManagementFilter, token?: string) {
    setList({ kind: 'loading' })
    setError(null)
    setSelected(null)
    setQuery((current) => ({ filter, token, reload: current.reload + 1 }))
  }

  const action = selected && ownershipAction(api, selected, ownerTeam, reason)

  async function submit() {
    if (!selected || !action) return
    const fingerprint = JSON.stringify([selected.recordId, action.payload])
    const previous = lastRequest.current
    const key = previous?.fingerprint === fingerprint ? previous.key : crypto.randomUUID()
    lastRequest.current = { fingerprint, key }
    setSubmitting(true)
    setMessage('')
    setError(null)
    try {
      const result = await action.send(key)
      setMessage(`${action.doneLabel}が完了しました。所有チーム ${result.ownerTeam}。${result.a2aBackfillRequired ? 'A2A 検索用 metadata の補完が必要です。' : ''}`)
      setList((current) => current.kind === 'ready'
        ? { kind: 'ready', page: { ...current.page, items: current.page.items.filter((item) => item.recordId !== selected.recordId) } }
        : current)
      setSelected(null)
      lastRequest.current = null
    } catch (failure) {
      setError(toApiError(failure, '管理操作の結果を確認できません。同じ内容で再試行してください。'))
    } finally {
      setSubmitting(false)
    }
  }

  const loading = list.kind === 'loading'
  return <section aria-labelledby="administration-heading">
    <h2 id="administration-heading">Registry 管理</h2>
    <h3>接続情報</h3>
    {connectionError && <RequestErrorAlert error={connectionError} onRelogin={onRelogin} />}
    {!connection && !connectionError && <p role="status">接続情報を取得しています…</p>}
    {connection && <>
      <dl><dt>Registry ARN</dt><dd>{connection.registryArn}</dd><dt>リージョン</dt><dd>{connection.region}</dd>
        <dt>認可方式</dt><dd>{connection.authorization ?? '未取得'}</dd>
        <dt>接続</dt><dd>{connection.connection.state === 'CONNECTED' ? '接続情報取得済み' : connection.connection.message}</dd>
        <dt>自動承認</dt><dd>{AUTO_APPROVAL_LABELS[connection.autoApproval.state]}</dd>
      </dl>
      {connection.warnings.map((warning) => <p role="alert" key={warning}>{warning}</p>)}
    </>}
    <h3>レコードの取り込み・所有チームの変更</h3>
    <div className="actions">
      <button type="button" disabled={loading || submitting} onClick={() => show('unmanaged')}>未管理レコード</button>
      <button type="button" disabled={loading || submitting} onClick={() => show('managed')}>管理済みレコード</button>
      <button type="button" disabled={loading || submitting} onClick={() => show(query.filter)}>一覧を再読み込み</button>
    </div>
    {list.kind === 'error' && <RequestErrorAlert error={list.error} onRelogin={onRelogin} />}
    {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
    {error?.status === 409 && <p>一覧を再読み込みして最新の管理情報を確認してください。</p>}
    {message && <p role="status">{message}</p>}
    {loading && <p role="status">レコードを取得しています…</p>}
    {list.kind === 'ready' && <>
      {list.page.items.length === 0 && <p>{query.filter === 'unmanaged' ? '未管理レコードはありません。' : 'このページに管理済みレコードはありません。'}{list.page.nextToken ? '次のページも確認してください。' : ''}</p>}
      <ul>{list.page.items.map((record) => <li key={record.recordId}>
        <strong>{record.displayName}</strong> {record.recordType}・{record.status}・{record.recordVersion}
        {record.ownerTeam && <span>・所有チーム {record.ownerTeam}</span>}
        <button type="button" disabled={submitting} onClick={() => { setSelected(record); setOwnerTeam(record.ownerTeam ?? ''); setReason(''); setMessage('') }}>{record.displayName} を選択</button>
      </li>)}</ul>
      {list.page.nextToken && <button type="button" disabled={submitting} onClick={() => show(query.filter, list.page.nextToken ?? undefined)}>次のページ</button>}
    </>}
    {selected && action && <form onSubmit={(event) => { event.preventDefault(); void submit() }}>
      <h4>{selected.displayName} の{action.title}</h4>
      <label>所有チーム<input name="adminOwnerTeam" value={ownerTeam} maxLength={128} onChange={(event) => setOwnerTeam(event.target.value)} disabled={submitting} required /></label>
      <p>既存の Cognito グループ名を team- から入力してください。</p>
      <label>理由<input name="adminReason" value={reason} maxLength={4096} onChange={(event) => setReason(event.target.value)} disabled={submitting} required /></label>
      <button type="submit" disabled={submitting || !/^team-[A-Za-z0-9_./-]{1,123}$/.test(ownerTeam) || !reason.trim()}>{submitting ? '結果を確認しています…' : action.submitLabel}</button>
    </form>}
    <p className="note">取り込み後は所有チームの Publisher が通常の編集・申請・公開承認を進めてください。取り込んだレコードは既存 metadata を保持し、descriptor から isA2A を補完します。検索の全ページ照合が完了するまで A2A 検索を有効にしません。取り込み前の承認者は履歴で「不明」と表示します。</p>
  </section>
}
