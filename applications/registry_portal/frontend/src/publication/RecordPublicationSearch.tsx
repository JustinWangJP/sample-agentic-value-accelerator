import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState } from '../components/StatePanel'
import { recordStatusLabel } from '../drafts/api'
import {
  PUBLICATION_SEARCH_STATUSES,
  SUSPENDED_SEARCH_STATUS,
  isPublicationSearchStatus,
  type PublicationRecordPage,
  type PublicationSearchRequest,
  type PublicationSearchStatus,
} from './api'
import { RecordPublicationPanel } from './RecordPublicationPanel'
import { badge, button, card, fieldLabel, note } from '../ui/classes'

type Props = {
  api: ApiClient
  onRelogin: () => void
  /** 検索フォームの横に並べる要素（レコード ID の指定など） */
  aside?: ReactNode
}

/** 適用中の検索条件。次のページを読むときも、この条件をそのまま使う */
type Query = Omit<PublicationSearchRequest, 'nextToken'>

type PageState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; page: PublicationRecordPage }
  | { kind: 'error'; error: ApiError }

/** 公開停止中の表示。検索条件の選択肢と、一覧の項目の区別で同じ文言を使う */
const SUSPENDED_LABEL = '公開停止中'

/** 検索条件の状態のラベル。公開停止中だけポータルの区別の表示で、他は AWS の状態の既存の表示（recordStatusLabel）に合わせる */
function searchStatusLabel(status: PublicationSearchStatus): string {
  return status === SUSPENDED_SEARCH_STATUS ? SUSPENDED_LABEL : recordStatusLabel(status)
}

/**
 * Curator が管理下のレコードを名前の一部と状態で探す検索一覧。公開停止中などカタログに出ない
 * レコードもここで探して公開管理を開ける。所有チームでは絞り込まない
 */
export function RecordPublicationSearch({ api, onRelogin, aside }: Props) {
  const [name, setName] = useState('')
  const [status, setStatus] = useState<'' | PublicationSearchStatus>('')
  const [query, setQuery] = useState<Query | null>(null)
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState<PageState>({ kind: 'idle' })

  useEffect(() => {
    if (query === null) return
    let active = true
    api.searchPublicationRecords({ ...query, nextToken: token }).then(
      (page) => {
        if (active) setState({ kind: 'ready', page })
      },
      (failure: unknown) => {
        if (active)
          setState({ kind: 'error', error: toApiError(failure, '管理レコードの検索に失敗しました。') })
      },
    )
    return () => {
      active = false
    }
  }, [api, query, token, revision])

  /**
   * 同じ条件と同じページで読み直す。検索失敗の再試行で使う。読み直しの間は一覧を外すため、
   * 開いている公開管理（操作の結果表示を含む）も閉じる。そのため公開管理の操作後には呼ばない
   */
  function reload() {
    setState({ kind: 'loading' })
    setRevision((value) => value + 1)
  }

  function search(event: FormEvent) {
    event.preventDefault()
    setQuery({ name: name.trim(), status: status === '' ? null : status })
    setToken(undefined)
    setState({ kind: 'loading' })
  }

  return (
    <section aria-label="管理レコードの検索" className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
      <div className={`${card} p-5 space-y-3 lg:col-span-2`}>
        <h2 className="text-sm font-semibold text-slate-900">管理レコードの検索</h2>
        <p className={note}>名前の一部と状態で、管理下のレコードを探せます。所有チームでは絞り込みません。</p>
        <form onSubmit={search} className="flex flex-col sm:flex-row sm:items-end gap-2.5">
          <label className={`${fieldLabel} flex-1`}>
            名前（部分一致）
            <input
              name="publicationSearchName"
              type="search"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className={fieldLabel}>
            状態
            <select
              name="publicationSearchStatus"
              value={status}
              onChange={(event) => {
                const value = event.target.value
                setStatus(value === '' ? '' : isPublicationSearchStatus(value) ? value : '')
              }}
            >
              <option value="">すべて</option>
              {PUBLICATION_SEARCH_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {searchStatusLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={`${button.primary} shrink-0`}>
            検索する
          </button>
        </form>
      </div>
      {aside}
      </div>
      {state.kind === 'loading' && <LoadingState>管理レコードを検索しています…</LoadingState>}
      {state.kind === 'error' && (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={reload} />
      )}
      {state.kind === 'ready' && state.page.items.length === 0 && (
        <EmptyState hint={state.page.nextToken ? undefined : '状態や名前の条件を変えてください。'}>
          {state.page.nextToken
            ? 'このページには条件に一致する管理レコードがありません。次のページを確認してください。'
            : '条件に一致する管理レコードはありません。'}
        </EmptyState>
      )}
      {state.kind === 'ready' && (
        <div className={`${card} overflow-hidden`}>
          <div className="px-5 py-3.5 border-b border-slate-200 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-900">管理レコードの検索結果</h3>
            <span className="text-xs text-slate-500">所有チームによらず管理下のレコードを表示します</span>
          </div>
          {state.page.items.length > 0 && (
            <div className={`hidden md:grid ${RESULT_COLUMNS} bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600`} aria-hidden="true">
              <span className="py-3 px-4">レコード名 / ID</span>
              <span className="py-3 px-4">種別・所有チーム</span>
              <span className="py-3 px-4">版 / 公開中の版</span>
              <span className="py-3 px-4">状態</span>
              <span className="py-3 px-4 text-right">操作</span>
            </div>
          )}
          <ul className="divide-y divide-slate-200 text-sm">
            {state.page.items.map((item) => (
              <PublicationResult key={item.recordId} api={api} item={item} onRelogin={onRelogin} />
            ))}
          </ul>
          <nav className="px-4 py-3 border-t border-slate-200 bg-slate-50 flex items-center justify-end gap-2 text-xs" aria-label="管理レコード検索のページ">
            <button
              type="button"
              className={button.small}
              disabled={token === undefined}
              onClick={() => {
                setToken(undefined)
                setState({ kind: 'loading' })
              }}
            >
              最初のページ
            </button>
            <button
              type="button"
              className={button.small}
              disabled={state.page.nextToken === null}
              onClick={() => {
                if (state.page.nextToken) {
                  setToken(state.page.nextToken)
                  setState({ kind: 'loading' })
                }
              }}
            >
              次のページ
            </button>
          </nav>
        </div>
      )}
    </section>
  )
}

const RESULT_COLUMNS = 'md:grid-cols-[minmax(0,2fr)_minmax(0,1.3fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_9rem]'

/** 検索結果の 1 件。公開管理を開くと、その項目の下にパネルを展開する */
function PublicationResult({
  api,
  item,
  onRelogin,
}: {
  api: ApiClient
  item: PublicationRecordPage['items'][number]
  onRelogin: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <li className={open ? 'bg-blue-50/40' : 'hover:bg-slate-50/70'}>
      <div className={`grid grid-cols-1 ${RESULT_COLUMNS} items-center`}>
        <div className="py-3 px-4 min-w-0">
          <h4 className="font-semibold text-slate-900 break-all">{item.displayName}</h4>
          <p className="text-xs font-mono text-slate-500 break-all">
            {item.name} · {item.recordId}
          </p>
        </div>
        <p className="px-4 md:py-3 text-xs text-slate-700">
          {RECORD_TYPE_LABELS[item.recordType]}・{item.ownerTeam ?? '所有チーム不明'}
        </p>
        <div className="px-4 md:py-3 text-xs font-mono">
          <p>版 {item.recordVersion}</p>
          <p className={item.publishedVersion ? 'text-emerald-700' : 'text-slate-400'}>
            {item.publishedVersion ? `公開中の版 ${item.publishedVersion}` : '公開中の版なし'}
          </p>
        </div>
        <p className="px-4 md:py-3 text-xs">
          <span className={item.suspended ? SUSPENDED_BADGE : badge.neutral}>
            状態 {searchStatusLabel(item.status)}
            {item.suspended ? `・${SUSPENDED_LABEL}` : ''}
          </span>
        </p>
        <div className="py-3 px-4 md:text-right">
          <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)} className={button.small}>
            {open ? '公開管理を閉じる' : '公開管理を開く'}
          </button>
        </div>
      </div>
      {open && (
        <div className="px-4 pb-4">
          <RecordPublicationPanel api={api} recordId={item.recordId} onRelogin={onRelogin} />
        </div>
      )}
    </li>
  )
}

const SUSPENDED_BADGE =
  'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-orange-50 text-orange-800 ring-1 ring-inset ring-orange-600/20'
