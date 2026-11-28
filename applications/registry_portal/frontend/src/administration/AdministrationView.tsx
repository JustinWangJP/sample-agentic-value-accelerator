import { useEffect, useRef, useState } from 'react'
import { toApiError, type ApiError, type Role } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { badge, button, callout, card, fieldLabel, note } from '../ui/classes'
import { SUBMISSION_REASON_MAX_LENGTH } from '../drafts/api'
import {
  OWNER_TEAM_MAX_LENGTH,
  OWNER_TEAM_PREFIX,
  isOwnerTeam,
  type AdminPage,
  type AdminRecord,
  type AdministrationApi,
  type AdministrationResult,
  type AutoApprovalState,
  type ManagementFilter,
  type RegistryConnection,
} from './api'

type Props = { api: AdministrationApi; roles: readonly Role[]; onRelogin: () => void }
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
      setMessage(`${action.doneLabel}が完了しました。所有チーム ${result.ownerTeam}。${result.a2aBackfillRequired ? 'A2A 検索用 metadata の補完が必要です。所有チームが保存・申請・公開承認すると補完されます。' : ''}`)
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
  return <section aria-labelledby="administration-heading" className="space-y-6">
    <PageHeader
      headingId="administration-heading"
      title="Registry 管理"
      description={<p>接続先の AWS Agent Registry の設定状態の確認、未管理レコードの取り込み、管理レコードの所有チームの変更を行います。</p>}
    />
    <section className={`${card} p-5 space-y-4`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">接続情報</h2>
        {connection && (
          <span className={connection.connection.state === 'CONNECTED' ? badge.success : badge.danger}>
            {connection.connection.state === 'CONNECTED' ? '接続情報取得済み' : '接続に問題があります'}
          </span>
        )}
      </div>
      {connectionError && <RequestErrorAlert error={connectionError} onRelogin={onRelogin} />}
      {!connection && !connectionError && <LoadingState>接続情報を取得しています…</LoadingState>}
      {connection && <>
        <dl className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 text-xs">
          <div className="md:col-span-2">
            <dt className="text-slate-500">Registry ARN</dt>
            <dd className="mt-1 font-mono text-slate-800 bg-slate-50 px-2.5 py-1.5 rounded border border-slate-200 break-all">{connection.registryArn}</dd>
          </div>
          <div>
            <dt className="text-slate-500">リージョン</dt>
            <dd className="mt-1 font-mono text-slate-900">{connection.region}</dd>
          </div>
          <div>
            <dt className="text-slate-500">認可方式</dt>
            <dd className={`mt-1 font-mono ${connection.authorization ? 'text-slate-900' : 'text-amber-800'}`}>{connection.authorization ?? '未取得'}</dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-slate-500">接続</dt>
            <dd className="mt-1 text-slate-900">{connection.connection.state === 'CONNECTED' ? '接続情報取得済み' : `${connection.connection.message}（原因: ${connection.connection.cause}）`}</dd>
          </div>
          <div>
            <dt className="text-slate-500">自動承認</dt>
            <dd className={`mt-1 font-semibold ${connection.autoApproval.state === 'UNKNOWN' ? 'text-amber-800' : 'text-slate-900'}`}>{AUTO_APPROVAL_LABELS[connection.autoApproval.state]}</dd>
          </div>
        </dl>
        {connection.warnings.map((warning) => <p role="alert" key={warning} className={callout.warning}>{warning}</p>)}
      </>}
    </section>
    <section className={`${card} overflow-hidden`} aria-labelledby="administration-records-heading">
      <div className="px-5 pt-4 space-y-3">
        <h2 id="administration-records-heading" className="text-sm font-semibold text-slate-900">レコードの取り込み・所有チームの変更</h2>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200">
          <div className="flex items-center gap-1 -mb-px">
            {(['unmanaged', 'managed'] as const).map((filter) => (
              <button
                key={filter}
                type="button"
                aria-pressed={query.filter === filter}
                className={query.filter === filter ? FILTER_TAB.selected : FILTER_TAB.normal}
                disabled={loading || submitting}
                onClick={() => show(filter)}
              >
                {FILTER_LABELS[filter]}
              </button>
            ))}
          </div>
          <button type="button" className={`${button.small} mb-2`} disabled={loading || submitting} onClick={() => show(query.filter)}>一覧を再読み込み</button>
        </div>
      </div>
      <div className="p-5 space-y-4">
        {list.kind === 'error' && <RequestErrorAlert error={list.error} onRelogin={onRelogin} />}
        {error && <RequestErrorAlert error={error} onRelogin={onRelogin}>
          {error.status === 409 && <p className="text-xs text-rose-800">一覧を再読み込みして最新の管理情報を確認してください。</p>}
        </RequestErrorAlert>}
        {message && <p role="status" className={callout.success}>{message}</p>}
        {loading && <LoadingState>レコードを取得しています…</LoadingState>}
        {list.kind === 'ready' && <>
          {list.page.items.length === 0 && <EmptyState>{query.filter === 'unmanaged' ? '未管理レコードはありません。' : 'このページに管理済みレコードはありません。'}{list.page.nextToken ? '次のページも確認してください。' : ''}</EmptyState>}
          {list.page.items.length > 0 && <ul className="divide-y divide-slate-200 border border-slate-200 rounded-lg overflow-hidden text-sm">{list.page.items.map((record) => <li key={record.recordId} className={selected?.recordId === record.recordId ? 'bg-blue-50/40' : 'hover:bg-slate-50/70'}>
            <div className="px-4 py-3 flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <strong className="font-semibold text-slate-900 break-all">{record.displayName}</strong>
                <p className="text-xs font-mono text-slate-500 break-all">{record.recordType}・{record.status}・{record.recordVersion}
                  {record.ownerTeam ? <span>・所有チーム {record.ownerTeam}</span> : <span className="italic">・所有チーム未設定（未管理）</span>}
                </p>
              </div>
              <button type="button" className={button.small} disabled={submitting} onClick={() => { setSelected(record); setOwnerTeam(record.ownerTeam ?? ''); setReason(''); setMessage('') }}>{record.displayName} を選択</button>
            </div>
          </li>)}</ul>}
          {list.page.nextToken && <div className="flex justify-center"><button type="button" className={button.small} disabled={submitting} onClick={() => show(query.filter, list.page.nextToken ?? undefined)}>次のページ</button></div>}
        </>}
      </div>
      {selected && action && <form className="p-6 bg-slate-50 border-t border-slate-200 space-y-4" onSubmit={(event) => { event.preventDefault(); void submit() }}>
        <h3 className="text-sm font-semibold text-slate-900 break-all">{selected.displayName} の{action.title}</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl">
          <label className={fieldLabel}>所有チーム<input name="adminOwnerTeam" className="font-mono" value={ownerTeam} maxLength={OWNER_TEAM_MAX_LENGTH} onChange={(event) => setOwnerTeam(event.target.value)} disabled={submitting} required /></label>
          <label className={fieldLabel}>理由<input name="adminReason" value={reason} maxLength={SUBMISSION_REASON_MAX_LENGTH} onChange={(event) => setReason(event.target.value)} disabled={submitting} required /></label>
        </div>
        <p className={note}>既存の Cognito グループ名を {OWNER_TEAM_PREFIX} から入力してください。</p>
        <button type="submit" className={button.primary} disabled={submitting || !isOwnerTeam(ownerTeam) || !reason.trim()}>{submitting ? '結果を確認しています…' : action.submitLabel}</button>
      </form>}
      <p className={`px-5 pb-5 pt-1 ${note}`}>取り込みは Registry を更新しません。取り込み後は所有チームの Publisher が通常の編集・申請・公開承認を進めてください。既存 metadata は保持され、A2A 検索用の isA2A は所有チームが保存したときに descriptor から補完されます（編集画面にも補完が必要なことを表示します）。検索の全ページ照合が完了するまで A2A 検索を有効にしません。取り込み前の承認者は履歴で「不明」と表示します。</p>
    </section>
  </section>
}

const FILTER_LABELS: Record<ManagementFilter, string> = { unmanaged: '未管理レコード', managed: '管理済みレコード' }
const FILTER_TAB = {
  selected: 'px-4 py-2.5 text-sm font-semibold border-b-2 border-blue-600 text-blue-700 bg-blue-50/70 rounded-t-lg disabled:opacity-50',
  normal: 'px-4 py-2.5 text-sm font-medium border-b-2 border-transparent text-slate-600 hover:text-slate-900 rounded-t-lg disabled:opacity-50',
}
