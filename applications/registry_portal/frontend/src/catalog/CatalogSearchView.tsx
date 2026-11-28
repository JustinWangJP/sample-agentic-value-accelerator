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
import { DATA_CLASSIFICATIONS, DATA_CLASSIFICATION_LABELS } from '../api/dataClassification'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState } from '../components/StatePanel'
import { button, callout, card } from '../ui/classes'
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

const FILTER_LABEL = 'flex items-center gap-2 text-xs font-medium text-slate-600'
const FILTER_SELECT = 'w-auto py-1.5 px-2.5 text-xs'

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
      <section aria-labelledby="catalog-search-heading" className={`${card} p-4 sm:p-5 mb-6`}>
        <h2 id="catalog-search-heading" className="sr-only">
          カタログを検索
        </h2>
        <form className="space-y-3" onSubmit={submit}>
          <div className="flex flex-col sm:flex-row sm:items-end gap-2.5">
            <label className="flex-1 flex flex-col gap-1.5 text-xs font-medium text-slate-600">
              自然言語またはキーワード
              <span className="relative block">
                <svg
                  className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z" />
                </svg>
                <input
                  name="query"
                  type="search"
                  required
                  pattern=".*\S.*"
                  maxLength={256}
                  placeholder="例: 引受審査、社内規定、請求書チェック"
                  className="pl-10 py-2.5"
                />
              </span>
            </label>
            <div className="flex gap-2 shrink-0">
              <button type="submit" className={`${button.primary} py-2.5`}>
                検索する
              </button>
              {view.status !== 'idle' && (
                <button type="button" className={`${button.secondary} py-2.5`} onClick={() => setView({ status: 'idle' })}>
                  一覧に戻る
                </button>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
            <label className={FILTER_LABEL}>
              種別
              <select
                name="type"
                className={FILTER_SELECT}
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
            <label className={FILTER_LABEL}>
              A2A
              <select name="a2a" className={FILTER_SELECT} disabled={!supportsA2AFilter(recordType)}>
                <option value="">指定なし</option>
                <option value="true">A2A の Agent</option>
                <option value="false">A2A 以外の Agent</option>
              </select>
            </label>
            <label className={FILTER_LABEL}>
              データ分類
              <select name="dataClassification" className={FILTER_SELECT}>
                <option value="">すべて</option>
                {DATA_CLASSIFICATIONS.map((classification) => (
                  <option key={classification} value={classification}>
                    {DATA_CLASSIFICATION_LABELS[classification]}
                  </option>
                ))}
              </select>
            </label>
            <span className="text-xs text-slate-400 sm:ml-auto">
              ※ 検索は検索語が必須です。条件だけで探す場合は下の一覧を使います。
            </span>
          </div>
        </form>
        {view.status !== 'idle' && (
          <div aria-label="検索結果" role="region" className="mt-5 pt-5 border-t border-slate-200 space-y-4">
            {view.status === 'loading' ? (
              <LoadingState>検索しています…</LoadingState>
            ) : view.status === 'error' ? (
              view.error.code === A2A_SEARCH_NOT_READY ? (
                // 準備ができるまでは再試行しても解決しないので、一覧への入口だけを示す
                <RequestErrorAlert error={view.error} onRelogin={onRelogin}>
                  <button type="button" className={button.warningOutline} onClick={browseA2A}>
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
        <p role="note" className={callout.warning}>
          A2A の検索用情報と定義が一致しない {result.inconsistentA2ACount}{' '}
          件を結果から除外しました。所有チームまたは管理者に確認してください。
        </p>
      )}
      {result.items.length === 0 ? (
        <EmptyState>
          <span role="status">条件に一致するカタログ項目はありません。</span>
        </EmptyState>
      ) : (
        <CatalogItems items={result.items} onOpenDetail={onOpenDetail} />
      )}
    </>
  )
}
