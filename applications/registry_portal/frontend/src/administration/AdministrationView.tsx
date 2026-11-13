import { useEffect, useRef, useState } from 'react'
import { toApiError, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import type { AdminPage, AdminRecord, AdministrationApi, ManagementFilter, RegistryConnection } from './api'

type Props = { api: AdministrationApi; roles: readonly string[]; onRelogin: () => void }
export function AdministrationView(props: Props) {
  return props.roles.includes('Admin') ? <AdministrationPanel {...props} /> : null
}

function AdministrationPanel({ api, onRelogin }: Props) {
  const [connection, setConnection] = useState<RegistryConnection | null>(null)
  const [connectionError, setConnectionError] = useState<ApiError | null>(null)
  const [filter, setFilter] = useState<ManagementFilter>('unmanaged')
  const [page, setPage] = useState<AdminPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [selected, setSelected] = useState<AdminRecord | null>(null)
  const [ownerTeam, setOwnerTeam] = useState('')
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const lastRequest = useRef<{ fingerprint: string; key: string } | null>(null)
  const generation = useRef(0)

  useEffect(() => {
    let active = true
    api.getRegistryConnection().then((result) => {
      if (active) setConnection(result)
    }).catch((failure: unknown) => {
      if (active) setConnectionError(toApiError(failure, '接続情報を取得できませんでした。'))
    })
    return () => { active = false }
  }, [api])

  useEffect(() => {
    let active = true
    const current = ++generation.current
    api.listAdminRecords('unmanaged').then((result) => {
      if (active && current === generation.current) setPage(result)
    }).catch((failure: unknown) => {
      if (active && current === generation.current) setError(toApiError(failure, '管理レコードを取得できませんでした。'))
    })
    return () => { active = false; generation.current += 1 }
  }, [api])

  async function load(nextFilter: ManagementFilter, token?: string) {
    const current = ++generation.current
    setLoading(true)
    setError(null)
    setPage(null)
    setSelected(null)
    setFilter(nextFilter)
    try {
      const result = await api.listAdminRecords(nextFilter, token)
      if (current === generation.current) setPage(result)
    } catch (failure) {
      if (current === generation.current) setError(toApiError(failure, '管理レコードを取得できませんでした。'))
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }

  async function submit() {
    if (!selected) return
    const payload = selected.managed
      ? { ownerTeam, reason, expectedManagementHash: selected.managementHash }
      : { ownerTeam, reason }
    const fingerprint = JSON.stringify([selected.recordId, payload])
    const previous = lastRequest.current
    const key = previous?.fingerprint === fingerprint ? previous.key : crypto.randomUUID()
    lastRequest.current = { fingerprint, key }
    setSubmitting(true)
    setMessage('')
    setError(null)
    try {
      const result = selected.managed
        ? await api.reassignOwner(selected.recordId, { ownerTeam, reason, expectedManagementHash: selected.managementHash }, key)
        : await api.adoptRecord(selected.recordId, { ownerTeam, reason }, key)
      setMessage(`${selected.managed ? '所有チームの変更' : '取り込み'}が完了しました。所有チーム ${result.ownerTeam}。${result.a2aBackfillRequired ? 'A2A 検索用 metadata の補完が必要です。' : ''}`)
      setPage((current) => current && ({ ...current, items: current.items.filter((item) => item.recordId !== selected.recordId) }))
      setSelected(null)
      lastRequest.current = null
    } catch (failure) {
      setError(toApiError(failure, '管理操作の結果を確認できません。同じ内容で再試行してください。'))
    } finally {
      setSubmitting(false)
    }
  }

  return <section aria-labelledby="administration-heading">
    <h2 id="administration-heading">Registry 管理</h2>
    <h3>接続情報</h3>
    {connectionError && <RequestErrorAlert error={connectionError} onRelogin={onRelogin} />}
    {!connection && !connectionError && <p role="status">接続情報を取得しています…</p>}
    {connection && <>
      <dl><dt>Registry ARN</dt><dd>{connection.registryArn}</dd><dt>リージョン</dt><dd>{connection.region}</dd>
        <dt>認可方式</dt><dd>{connection.authorization ?? '未取得'}</dd>
        <dt>接続</dt><dd>{connection.connection.state === 'CONNECTED' ? '接続情報取得済み' : connection.connection.message}</dd>
        <dt>自動承認</dt><dd>{({ DISABLED: '無効', ENABLED: '有効', UNKNOWN: '未取得' })[connection.autoApproval.state]}</dd>
      </dl>
      {connection.warnings.map((warning) => <p role="alert" key={warning}>{warning}</p>)}
    </>}
    <h3>レコードの取り込み・所有チームの変更</h3>
    <div className="actions">
      <button type="button" disabled={loading || submitting} onClick={() => void load('unmanaged')}>未管理レコード</button>
      <button type="button" disabled={loading || submitting} onClick={() => void load('managed')}>管理済みレコード</button>
      <button type="button" disabled={loading || submitting} onClick={() => void load(filter)}>一覧を再読み込み</button>
    </div>
    {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
    {error?.status === 409 && <p>一覧を再読み込みして最新の管理情報を確認してください。</p>}
    {message && <p role="status">{message}</p>}
    {loading || (!page && !error) ? <p role="status">レコードを取得しています…</p> : null}
    {page && <>
      {page.items.length === 0 && <p>{filter === 'unmanaged' ? '未管理レコードはありません。' : 'このページに管理済みレコードはありません。'}{page.nextToken ? '次のページも確認してください。' : ''}</p>}
      <ul>{page.items.map((record) => <li key={record.recordId}>
        <strong>{record.displayName}</strong> {record.recordType}・{record.status}・{record.recordVersion}
        {record.ownerTeam && <span>・所有チーム {record.ownerTeam}</span>}
        <button type="button" disabled={submitting} onClick={() => { setSelected(record); setOwnerTeam(record.ownerTeam ?? ''); setReason(''); setMessage('') }}>{record.displayName} を選択</button>
      </li>)}</ul>
      {page.nextToken && <button type="button" disabled={submitting || loading} onClick={() => void load(filter, page.nextToken ?? undefined)}>次のページ</button>}
    </>}
    {selected && <form onSubmit={(event) => { event.preventDefault(); void submit() }}>
      <h4>{selected.displayName} の{selected.managed ? '所有チーム変更' : '取り込み'}</h4>
      <label>所有チーム<input name="adminOwnerTeam" value={ownerTeam} maxLength={128} onChange={(event) => setOwnerTeam(event.target.value)} disabled={submitting} required /></label>
      <p>既存の Cognito グループ名を team- から入力してください。</p>
      <label>理由<input name="adminReason" value={reason} maxLength={4096} onChange={(event) => setReason(event.target.value)} disabled={submitting} required /></label>
      <button type="submit" disabled={submitting || !/^team-[A-Za-z0-9_./-]{1,123}$/.test(ownerTeam) || !reason.trim()}>{submitting ? '結果を確認しています…' : selected.managed ? '所有チームを変更する' : '取り込む'}</button>
    </form>}
    <p className="note">取り込み後は所有チームの Publisher が通常の編集・申請・公開承認を進めてください。既存 metadata を保持し、descriptor から isA2A を補完します。検索の全ページ照合が完了するまで A2A 検索を有効にしません。取り込み前の承認者は履歴で「不明」と表示します。</p>
  </section>
}
