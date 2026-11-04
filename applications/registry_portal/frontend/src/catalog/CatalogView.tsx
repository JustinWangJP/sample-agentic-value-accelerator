import { useEffect, useState } from 'react'
import type { ApiClient, CatalogItem, CatalogRecordType } from '../api/client'
import {
  canGoNext,
  canGoPrevious,
  currentPageToken,
  failed,
  goNext,
  goPrevious,
  initialCatalogState,
  received,
  retry,
  selectType,
  type CatalogState,
} from './pager'

/** 画面の入口。A2A は Agent のプロトコル属性として表示する */
const CATALOG_TABS: { type: CatalogRecordType; label: string }[] = [
  { type: 'AGENT', label: 'Agent' },
  { type: 'MCP', label: 'MCP サーバー' },
  { type: 'SKILL', label: 'Skill' },
]

const labelOf = (type: CatalogRecordType) =>
  CATALOG_TABS.find((tab) => tab.type === type)?.label ?? type

type Props = {
  api: ApiClient
  /** ログインし直す（401 のとき） */
  onRelogin: () => void
}

/** カタログ項目を種別ごとにページ単位で一覧する */
export function CatalogView({ api, onRelogin }: Props) {
  const [state, setState] = useState<CatalogState>(() => initialCatalogState())
  const isLoading = state.view.status === 'loading'
  const { type } = state
  const token = currentPageToken(state)

  useEffect(() => {
    if (!isLoading) return
    // 種別やページを切り替えたら、古い応答は捨てる
    let active = true
    api.listCatalog(type, token).then(
      (page) => active && setState((current) => received(current, page)),
      (error: unknown) => active && setState((current) => failed(current, error)),
    )
    return () => {
      active = false
    }
  }, [api, isLoading, type, token])

  return (
    <CatalogPanel
      state={state}
      onSelectType={(next) => setState(selectType(next))}
      onNext={() => setState(goNext)}
      onPrevious={() => setState(goPrevious)}
      onRetry={() => setState(retry)}
      onRelogin={onRelogin}
    />
  )
}

type PanelProps = {
  state: CatalogState
  onSelectType: (type: CatalogRecordType) => void
  onNext: () => void
  onPrevious: () => void
  onRetry: () => void
  onRelogin: () => void
}

/** カタログの表示（状態だけから描画する） */
export function CatalogPanel({
  state,
  onSelectType,
  onNext,
  onPrevious,
  onRetry,
  onRelogin,
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
        {CATALOG_TABS.map((tab) => (
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

      <div role="tabpanel" aria-label={labelOf(state.type)}>
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
              : `公開承認済みの ${labelOf(state.type)} はありません。`}
          </p>
        ) : (
          <ul className="catalog-items">
            {view.page.items.map((item) => (
              <CatalogItemCard key={item.recordId} item={item} />
            ))}
          </ul>
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

function CatalogItemCard({ item }: { item: CatalogItem }) {
  return (
    <li className="catalog-item">
      <h3>{item.displayName || item.name}</h3>
      <p className="meta">
        <code>{item.name}</code>
        {item.recordVersion && <span>版 {item.recordVersion}</span>}
        {item.isA2A && <span className="badge">A2A</span>}
      </p>
      {item.description && <p>{item.description}</p>}
    </li>
  )
}
