import { useEffect, useState } from 'react'
import type { ApiClient, CatalogItem, CatalogRecordType } from '../api/client'
import { CatalogDetailView } from './CatalogDetailView'
import { CatalogItems } from './CatalogItems'
import { CatalogSearchView } from './CatalogSearchView'
import { CATALOG_TYPES, catalogTypeLabel } from './recordTypes'
import {
  canGoNext,
  canGoPrevious,
  currentPageToken,
  pageFailed,
  goNext,
  goPrevious,
  initialCatalogState,
  pageReceived,
  reloadPage,
  type CatalogState,
} from './pager'

type Props = {
  api: ApiClient
  /** ログインし直す（401 のとき） */
  onRelogin: () => void
}

/** カタログ項目を種別ごとにページ単位で一覧する */
export function CatalogView({ api, onRelogin }: Props) {
  const [state, setState] = useState<CatalogState>(() => initialCatalogState('AGENT'))
  const [selectedItem, setSelectedItem] = useState<CatalogItem | null>(null)
  const isLoading = state.view.status === 'loading'
  const { type } = state
  const token = currentPageToken(state)

  useEffect(() => {
    if (!isLoading) return
    // 種別やページを切り替えたら、古い応答は捨てる
    let active = true
    api.listCatalog(type, token).then(
      (page) => active && setState((current) => pageReceived(current, page)),
      (error: unknown) => active && setState((current) => pageFailed(current, error)),
    )
    return () => {
      active = false
    }
  }, [api, isLoading, type, token])

  return (
    <>
      <div hidden={selectedItem !== null}>
        <CatalogSearchView api={api} onRelogin={onRelogin} onOpenDetail={setSelectedItem}>
          <CatalogPanel
            state={state}
            onSelectType={(next) => setState(initialCatalogState(next))}
            onNext={() => setState(goNext)}
            onPrevious={() => setState(goPrevious)}
            onRetry={() => setState(reloadPage)}
            onRelogin={onRelogin}
            onOpenDetail={setSelectedItem}
          />
        </CatalogSearchView>
      </div>
      {selectedItem !== null && (
        <CatalogDetailView
          key={selectedItem.recordId}
          api={api}
          item={selectedItem}
          onRelogin={onRelogin}
          onClose={() => setSelectedItem(null)}
        />
      )}
    </>
  )
}

type PanelProps = {
  state: CatalogState
  onSelectType: (type: CatalogRecordType) => void
  onNext: () => void
  onPrevious: () => void
  onRetry: () => void
  onRelogin: () => void
  onOpenDetail: (item: CatalogItem) => void
}

/** カタログの表示（状態だけから描画する） */
export function CatalogPanel({
  state,
  onSelectType,
  onNext,
  onPrevious,
  onRetry,
  onRelogin,
  onOpenDetail,
}: PanelProps) {
  const { view } = state
  return (
    <section aria-labelledby="catalog-heading" className="catalog">
      <h2 id="catalog-heading">カタログ</h2>
      <p className="note">
        公開承認済みの Registry レコードだけを表示しています。公開承認は実行許可ではありません。
        利用する前に、所有チームの利用条件を確認してください。
      </p>
      <div role="tablist" aria-label="種別" className="tabs">
        {CATALOG_TYPES.map((tab) => (
          <button
            key={tab.type}
            type="button"
            role="tab"
            aria-selected={tab.type === state.type}
            onClick={() => onSelectType(tab.type)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" aria-label={catalogTypeLabel(state.type)}>
        {view.status === 'loading' ? (
          <p role="status">読み込んでいます…</p>
        ) : view.status === 'error' ? (
          <div role="alert" className="error">
            <p>{view.message}</p>
            <button type="button" onClick={onRetry}>
              再試行
            </button>
            {state.pageNumber > 1 && (
              // nextToken の期限切れなどで同じページを取り直しても解決しない場合の戻り道
              <button type="button" onClick={() => onSelectType(state.type)}>
                最初のページに戻る
              </button>
            )}
            {view.requiresLogin && (
              <button type="button" onClick={onRelogin}>
                もう一度ログインする
              </button>
            )}
          </div>
        ) : view.page.items.length === 0 ? (
          <p>
            {view.page.nextToken !== null
              ? 'このページには項目がありません。次のページを確認してください。'
              : `公開承認済みの ${catalogTypeLabel(state.type)} はありません。`}
          </p>
        ) : (
          <CatalogItems items={view.page.items} onOpenDetail={onOpenDetail} />
        )}

        <nav aria-label="ページ" className="pager">
          <button type="button" onClick={onPrevious} disabled={!canGoPrevious(state)}>
            前のページ
          </button>
          <span>ページ {state.pageNumber}</span>
          <button type="button" onClick={onNext} disabled={!canGoNext(state)}>
            次のページ
          </button>
        </nav>
      </div>
    </section>
  )
}
