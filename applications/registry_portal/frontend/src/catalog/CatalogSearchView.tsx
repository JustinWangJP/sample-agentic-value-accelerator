import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import {
  A2A_SEARCH_NOT_READY,
  supportsA2AFilter,
  type ApiClient,
  type ApiError,
  type CatalogItem,
  type CatalogRecordType,
  type CatalogSearchRequest,
  type CatalogSearchResult,
} from '../api/client'
import { DATA_CLASSIFICATIONS } from '../api/dataClassification'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { CatalogItems } from './CatalogItems'
import { CATALOG_TYPES } from './recordTypes'
import { settleWhileActive } from './requests'

type SearchView =
  | { status: 'idle' }
  | { status: 'loading'; request: CatalogSearchRequest }
  | { status: 'ready'; result: CatalogSearchResult; request: CatalogSearchRequest }
  | { status: 'error'; request: CatalogSearchRequest; error: ApiError }

type Props = {
  api: ApiClient
  onRelogin: () => void
  onOpenDetail: (item: CatalogItem) => void
  /** A2A の Agent 一覧を開く（A2A の絞り込み検索を使えないとき） */
  onBrowseA2A: () => void
  children: ReactNode
}

export function CatalogSearchView({ api, onRelogin, onOpenDetail, onBrowseA2A, children }: Props) {
  const [view, setView] = useState<SearchView>({ status: 'idle' })
  const [recordType, setRecordType] = useState<CatalogRecordType | null>(null)
  useEffect(() => {
    if (view.status !== 'loading') return
    const { request } = view
    return settleWhileActive(api.searchCatalog(request), 'カタログを検索できませんでした。', (settled) =>
      setView(
        settled.ok
          ? { status: 'ready', result: settled.value, request }
          : { status: 'error', error: settled.error, request },
      ),
    )
  }, [api, view])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    const query = String(values.get('query') ?? '').trim()
    // AWS の検索は検索語が必須。空白だけの入力は送らない
    if (!query) return
    const request: CatalogSearchRequest = { query }
    if (recordType) request.type = recordType
    const a2a = values.get('a2a')
    if (supportsA2AFilter(recordType) && (a2a === 'true' || a2a === 'false')) {
      request.a2a = a2a === 'true'
    }
    const classification = DATA_CLASSIFICATIONS.find(
      (value) => value === values.get('dataClassification'),
    )
    if (classification) request.dataClassification = classification
    setView({ status: 'loading', request })
  }

  function browseA2A() {
    onBrowseA2A()
    setView({ status: 'idle' })
  }

  return (
    <>
      <section aria-labelledby="catalog-search-heading" className="catalog">
        <h2 id="catalog-search-heading">カタログを検索</h2>
        <form className="catalog-search-form" onSubmit={submit}>
          <label className="catalog-search-query">
            自然言語またはキーワード
            <input name="query" type="search" required pattern=".*\S.*" maxLength={256} />
          </label>
          <div className="catalog-search-filters">
            <label>
              種別
              <select
                name="type"
                value={recordType ?? ''}
                onChange={(event) =>
                  setRecordType(
                    CATALOG_TYPES.find(({ type }) => type === event.target.value)?.type ?? null,
                  )
                }
              >
                <option value="">すべて</option>
                {CATALOG_TYPES.map(({ type, label }) => (
                  <option key={type} value={type}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              A2A
              <select name="a2a" disabled={!supportsA2AFilter(recordType)}>
                <option value="">指定なし</option>
                <option value="true">A2A の Agent</option>
                <option value="false">A2A 以外の Agent</option>
              </select>
            </label>
            <label>
              データ分類
              <select name="dataClassification">
                <option value="">すべて</option>
                {DATA_CLASSIFICATIONS.map((classification) => (
                  <option key={classification} value={classification}>{classification}</option>
                ))}
              </select>
            </label>
          </div>
          <button type="submit">検索する</button>
          {view.status !== 'idle' && (
            <button type="button" onClick={() => setView({ status: 'idle' })}>
              一覧に戻る
            </button>
          )}
        </form>
        {view.status !== 'idle' && (
          <div aria-label="検索結果" role="region">
            {view.status === 'loading' ? (
              <p role="status">検索しています…</p>
            ) : view.status === 'error' ? (
              view.error.code === A2A_SEARCH_NOT_READY ? (
                // 準備ができるまでは再試行しても解決しないので、一覧への入口だけを示す
                <RequestErrorAlert error={view.error} onRelogin={onRelogin}>
                  <button type="button" onClick={browseA2A}>
                    A2A の Agent 一覧を開く
                  </button>
                </RequestErrorAlert>
              ) : (
                <RequestErrorAlert
                  error={view.error}
                  onRelogin={onRelogin}
                  onRetry={() => setView({ status: 'loading', request: view.request })}
                />
              )
            ) : (
              <SearchResult result={view.result} onOpenDetail={onOpenDetail} />
            )}
          </div>
        )}
      </section>
      <div hidden={view.status !== 'idle'}>{children}</div>
    </>
  )
}

function SearchResult({
  result,
  onOpenDetail,
}: {
  result: CatalogSearchResult
  onOpenDetail: (item: CatalogItem) => void
}) {
  return (
    <>
      {result.inconsistentA2ACount > 0 && (
        <p role="note" className="note warning">
          A2A の検索用情報と定義が一致しない {result.inconsistentA2ACount}{' '}
          件を結果から除外しました。所有チームまたは管理者に確認してください。
        </p>
      )}
      {result.items.length === 0 ? (
        <p role="status">条件に一致するカタログ項目はありません。</p>
      ) : (
        <CatalogItems items={result.items} onOpenDetail={onOpenDetail} />
      )}
    </>
  )
}
