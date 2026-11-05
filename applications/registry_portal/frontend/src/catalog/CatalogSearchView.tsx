import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, type ApiClient, type CatalogItem, type CatalogSearchRequest } from '../api/client'
import { CatalogItems } from './CatalogItems'
import { CatalogRequestError } from './CatalogRequestError'
import { CATALOG_TYPES } from './recordTypes'

type SearchView =
  | { status: 'idle' }
  | { status: 'loading'; request: CatalogSearchRequest }
  | { status: 'ready'; items: CatalogItem[]; request: CatalogSearchRequest }
  | { status: 'error'; request: CatalogSearchRequest; error: ApiError }

type Props = {
  api: ApiClient
  onRelogin: () => void
  onOpenDetail: (item: CatalogItem) => void
  onBrowseA2a: () => void
  children: ReactNode
}

export function CatalogSearchView({ api, onRelogin, onOpenDetail, onBrowseA2a, children }: Props) {
  const [view, setView] = useState<SearchView>({ status: 'idle' })
  const [recordType, setRecordType] = useState('')
  useEffect(() => {
    if (view.status !== 'loading') return
    let active = true
    api.searchCatalog(view.request).then(
      (items) => active && setView({ status: 'ready', items, request: view.request }),
      (failure: unknown) => {
        if (!active) return
        const error =
          failure instanceof ApiError
            ? failure
            : new ApiError(0, 'UNEXPECTED_ERROR', 'カタログを検索できませんでした。')
        setView({ status: 'error', request: view.request, error })
      },
    )
    return () => {
      active = false
    }
  }, [api, view])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    const request: CatalogSearchRequest = { query: String(values.get('query') ?? '').trim() }
    const type = CATALOG_TYPES.find((entry) => entry.type === recordType)?.type
    if (type) request.type = type
    const a2a = values.get('a2a')
    if (a2a === 'true' || a2a === 'false') request.a2a = a2a === 'true'
    const classification = values.get('dataClassification')
    if (classification === 'public' || classification === 'private') {
      request.dataClassification = classification
    }
    setView({ status: 'loading', request })
  }

  return (
    <>
      <section aria-labelledby="catalog-search-heading" className="catalog">
        <h2 id="catalog-search-heading">カタログを検索</h2>
        <form className="catalog-search-form" onSubmit={submit}>
          <label className="catalog-search-query">
            自然言語またはキーワード
            <input name="query" type="search" maxLength={256} />
          </label>
          <div className="catalog-search-filters">
            <label>
              種別
              <select
                name="type"
                value={recordType}
                onChange={(event) => setRecordType(event.target.value)}
              >
                <option value="">すべて</option>
                {CATALOG_TYPES.map(({ type, label }) => (
                  <option key={type} value={type}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              A2A
              <select
                name="a2a"
                disabled={recordType !== '' && recordType !== 'AGENT'}
              >
                <option value="">指定なし</option>
                <option value="true">A2A の Agent</option>
                <option value="false">A2A 以外の Agent</option>
              </select>
            </label>
            <label>
              データ分類
              <select name="dataClassification">
                <option value="">すべて</option>
                <option value="public">public</option>
                <option value="private">private</option>
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
              <CatalogRequestError
                error={view.error}
                onRelogin={onRelogin}
                onRetry={() => setView({ status: 'loading', request: view.request })}
              />
            ) : view.items.length === 0 ? (
              <>
                <p role="status">
                  条件に一致するカタログ項目はありません。
                </p>
                {view.request.a2a === true && (
                  <button
                    type="button"
                    onClick={() => {
                      onBrowseA2a()
                      setView({ status: 'idle' })
                    }}
                  >
                    A2A の Agent 一覧を開く
                  </button>
                )}
              </>
            ) : (
              <CatalogItems items={view.items} onOpenDetail={onOpenDetail} />
            )}
          </div>
        )}
      </section>
      <div hidden={view.status !== 'idle'}>{children}</div>
    </>
  )
}
