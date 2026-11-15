import { useEffect, useState, type FormEvent } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { recordStatusLabel } from '../drafts/api'
import {
  PUBLICATION_SEARCH_STATUSES,
  SUSPENDED_SEARCH_STATUS,
  isPublicationSearchStatus,
  type PublicationRecordPage,
  type PublicationSearchRequest,
  type PublicationSearchStatus,
} from './api'
import { RecordPublicationToggle } from './RecordPublicationPanel'

type Props = { api: ApiClient; onRelogin: () => void }

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
export function RecordPublicationSearch({ api, onRelogin }: Props) {
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
    <section aria-label="管理レコードの検索">
      <h3>管理レコードの検索</h3>
      <p className="note">名前の一部と状態で、管理下のレコードを探せます。</p>
      <form onSubmit={search}>
        <label>
          名前（部分一致）
          <input
            name="publicationSearchName"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
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
        <button type="submit">検索する</button>
      </form>
      {state.kind === 'loading' && <p role="status">管理レコードを検索しています…</p>}
      {state.kind === 'error' && (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={reload} />
      )}
      {state.kind === 'ready' && state.page.items.length === 0 && (
        <p>
          {state.page.nextToken
            ? 'このページには条件に一致する管理レコードがありません。次のページを確認してください。'
            : '条件に一致する管理レコードはありません。'}
        </p>
      )}
      {state.kind === 'ready' && (
        <ul className="catalog-items">
          {state.page.items.map((item) => (
            <li key={item.recordId}>
              <h4>{item.displayName}</h4>
              <p>
                {RECORD_TYPE_LABELS[item.recordType]}・{item.name}・{item.ownerTeam ?? '所有チーム不明'}・版{' '}
                {item.recordVersion}
              </p>
              <p>
                状態 {searchStatusLabel(item.status)}
                {item.suspended ? `・${SUSPENDED_LABEL}` : ''} /{' '}
                {item.publishedVersion ? `公開中の版 ${item.publishedVersion}` : '公開中の版なし'}
              </p>
              <RecordPublicationToggle api={api} recordId={item.recordId} onRelogin={onRelogin} />
            </li>
          ))}
        </ul>
      )}
      {state.kind === 'ready' && (
        <nav className="pager" aria-label="管理レコード検索のページ">
          <button
            type="button"
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
      )}
    </section>
  )
}
