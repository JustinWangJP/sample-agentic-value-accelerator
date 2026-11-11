import { useEffect, useState } from 'react'
import { supportsA2AFilter, type ApiClient, type CatalogItem, type CatalogRecordType } from '../api/client'
import { CatalogDetailView } from './CatalogDetailView'
import { CatalogItems } from './CatalogItems'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { CatalogSearchView } from './CatalogSearchView'
import { CATALOG_TYPES, catalogTypeLabel } from './recordTypes'
import { settleWhileActive } from './requests'
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
  UNEXPECTED_CATALOG_ERROR,
  type CatalogState,
} from './pager'

type Props = {
  api: ApiClient
  /** カタログ詳細から操作履歴を開けるか（所有チームによらず履歴を読める Curator・Admin） */
  canViewHistory: boolean
  /** 公開停止・廃止を行える業務ロール（Curator）か */
  canManageLifecycle?: boolean
  /** ログインし直す（401 のとき） */
  onRelogin: () => void
}

/** カタログ項目を種別ごとにページ単位で一覧する */
export function CatalogView({ api, canViewHistory, canManageLifecycle = false, onRelogin }: Props) {
  const [state, setState] = useState<CatalogState>(() => initialCatalogState('AGENT'))
  const [selectedItem, setSelectedItem] = useState<CatalogItem | null>(null)
  const isLoading = state.view.status === 'loading'
  const { type, a2aOnly } = state
  const token = currentPageToken(state)

  useEffect(() => {
    if (!isLoading) return
    // 種別やページを切り替えたら、古い応答は捨てる
    return settleWhileActive(api.listCatalog(type, token, a2aOnly), UNEXPECTED_CATALOG_ERROR, (settled) =>
      setState((current) =>
        settled.ok ? pageReceived(current, settled.value) : pageFailed(current, settled.error),
      ),
    )
  }, [api, isLoading, type, a2aOnly, token])

  return (
    <>
      <div hidden={selectedItem !== null}>
        <CatalogSearchView
          api={api}
          onRelogin={onRelogin}
          onOpenDetail={setSelectedItem}
          onBrowseA2A={() => setState(initialCatalogState('AGENT', true))}
        >
          <CatalogPanel
            state={state}
            onSelectType={(next) => setState(initialCatalogState(next))}
            onFilterA2A={(checked) => setState(initialCatalogState('AGENT', checked))}
            onFirstPage={() => setState(initialCatalogState(type, a2aOnly))}
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
          canViewHistory={canViewHistory}
          canManageLifecycle={canManageLifecycle}
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
  onFilterA2A: (checked: boolean) => void
  onFirstPage: () => void
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
  onFilterA2A,
  onFirstPage,
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
        {supportsA2AFilter(state.type) && (
          <label>
            <input
              type="checkbox"
              name="a2aOnly"
              checked={state.a2aOnly}
              onChange={(event) => onFilterA2A(event.target.checked)}
            />
            A2A の Agent のみ
          </label>
        )}
        {view.status === 'loading' ? (
          <p role="status">読み込んでいます…</p>
        ) : view.status === 'error' ? (
          <RequestErrorAlert error={view} onRetry={onRetry} onRelogin={onRelogin}>
            {state.pageNumber > 1 && (
              // nextToken の期限切れなどで同じページを取り直しても解決しない場合の戻り道
              <button type="button" onClick={onFirstPage}>
                最初のページに戻る
              </button>
            )}
          </RequestErrorAlert>
        ) : view.page.items.length === 0 ? (
          <p>
            {view.page.nextToken !== null
              ? 'このページには項目がありません。次のページを確認してください。'
              : `公開承認済みの ${state.a2aOnly ? 'A2A の Agent' : catalogTypeLabel(state.type)} はありません。`}
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
