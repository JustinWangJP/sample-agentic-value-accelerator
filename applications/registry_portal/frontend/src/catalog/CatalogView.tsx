import { useEffect, useState } from 'react'
import { supportsA2AFilter, type ApiClient, type CatalogItem, type CatalogRecordType } from '../api/client'
import { CatalogDetailView } from './CatalogDetailView'
import { CatalogItems } from './CatalogItems'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { useNavigation } from '../routing/navigation'
import { RouteLink } from '../routing/NavigationProvider'
import { button, pager } from '../ui/classes'
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
  canManagePublication?: boolean
  /** 下書きの作成へ進めるか（Publisher） */
  canCreateDraft?: boolean
  /** ログインし直す（401 のとき） */
  onRelogin: () => void
}

/**
 * カタログ項目を種別ごとにページ単位で一覧する。詳細は #/catalog/<レコード ID> で開く。
 * 詳細を開いている間も一覧と検索の状態は残し、戻ったときに同じページを表示する
 */
export function CatalogView({ api, canViewHistory, canManagePublication = false, canCreateDraft = false, onRelogin }: Props) {
  const { route, navigate } = useNavigation()
  const detailId = route.screen === 'catalog' ? route.recordId : null
  const [state, setState] = useState<CatalogState>(() => initialCatalogState('AGENT'))
  // 一覧や検索結果から開いた項目。詳細の取得を待つ間の見出しに使う
  const [openedItem, setOpenedItem] = useState<CatalogItem | null>(null)
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

  function openDetail(item: CatalogItem) {
    setOpenedItem(item)
    navigate({ screen: 'catalog', recordId: item.recordId })
  }

  return (
    <>
      <div hidden={detailId !== null}>
        <PageHeader
          title="カタログ"
          description={
            <p>公開承認済みの Agent・MCP サーバー・Skill を、種別の一覧または自然言語・キーワードの検索で探せます。</p>
          }
          actions={
            canCreateDraft && (
              <RouteLink to={{ screen: 'drafts', view: { kind: 'new' } }} className={button.primary}>
                ＋ 下書きを作成
              </RouteLink>
            )
          }
        />
        <CatalogSearchView
          api={api}
          onRelogin={onRelogin}
          onOpenDetail={openDetail}
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
            onOpenDetail={openDetail}
          />
        </CatalogSearchView>
      </div>
      {detailId !== null && (
        <CatalogDetailView
          key={detailId}
          api={api}
          recordId={detailId}
          item={openedItem?.recordId === detailId ? openedItem : null}
          canViewHistory={canViewHistory}
          canManagePublication={canManagePublication}
          onRelogin={onRelogin}
          onClose={() => navigate({ screen: 'catalog', recordId: null })}
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

const TAB = {
  selected: 'px-4 py-2.5 text-sm font-semibold border-b-2 border-blue-600 text-blue-700 bg-blue-50/70 rounded-t-lg',
  normal: 'px-4 py-2.5 text-sm font-medium border-b-2 border-transparent text-slate-600 hover:text-slate-900 rounded-t-lg',
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
    <section aria-labelledby="catalog-heading">
      <h2 id="catalog-heading" className="sr-only">
        カタログ
      </h2>
      <p className="mb-4 text-xs text-slate-500">
        公開承認済みの Registry レコードだけを表示しています。公開承認は実行許可ではありません。
        利用する前に、所有チームの利用条件を確認してください。
      </p>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 mb-5">
        <div role="tablist" aria-label="種別" className="flex items-center gap-1 -mb-px">
          {CATALOG_TYPES.map((tab) => (
            <button
              key={tab.type}
              type="button"
              role="tab"
              aria-selected={tab.type === state.type}
              onClick={() => onSelectType(tab.type)}
              className={tab.type === state.type ? TAB.selected : TAB.normal}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {supportsA2AFilter(state.type) && (
          <label className="inline-flex items-center gap-2 text-sm text-slate-700 pb-2 select-none">
            <input
              type="checkbox"
              name="a2aOnly"
              checked={state.a2aOnly}
              onChange={(event) => onFilterA2A(event.target.checked)}
            />
            A2A の Agent のみ
          </label>
        )}
      </div>

      <div role="tabpanel" aria-label={catalogTypeLabel(state.type)}>
        {view.status === 'loading' ? (
          <LoadingState>読み込んでいます…</LoadingState>
        ) : view.status === 'error' ? (
          <RequestErrorAlert error={{ message: view.message, requiresLogin: view.requiresLogin }} onRetry={onRetry} onRelogin={onRelogin}>
            {state.pageNumber > 1 && (
              // nextToken の期限切れなどで同じページを取り直しても解決しない場合の戻り道
              <button type="button" className={button.dangerOutline} onClick={onFirstPage}>
                最初のページに戻る
              </button>
            )}
          </RequestErrorAlert>
        ) : view.page.items.length === 0 ? (
          <EmptyState hint={view.page.nextToken === null ? '別の種別タブを確認するか、検索をお試しください。' : undefined}>
            {view.page.nextToken !== null
              ? 'このページには項目がありません。次のページを確認してください。'
              : `公開承認済みの ${state.a2aOnly ? 'A2A の Agent' : catalogTypeLabel(state.type)} はありません。`}
          </EmptyState>
        ) : (
          <CatalogItems items={view.page.items} onOpenDetail={onOpenDetail} />
        )}

        <nav aria-label="ページ" className={pager}>
          <button type="button" className={button.small} onClick={onPrevious} disabled={!canGoPrevious(state)}>
            前のページ
          </button>
          <span>ページ {state.pageNumber}</span>
          <button type="button" className={button.small} onClick={onNext} disabled={!canGoNext(state)}>
            次のページ
          </button>
        </nav>
      </div>
    </section>
  )
}
