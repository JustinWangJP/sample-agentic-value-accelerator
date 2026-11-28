import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { ApiClient, ApiError, CatalogDetail, CatalogItem } from '../api/client'
import { DATA_CLASSIFICATION_LABELS } from '../api/dataClassification'
import { DetailBreadcrumb } from '../components/NavigationControls'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { LoadingState } from '../components/StatePanel'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import { RecordPublicationToggle } from '../publication/RecordPublicationPanel'
import { RouteLink } from '../routing/NavigationProvider'
import { badge, callout, card, pageTitle } from '../ui/classes'
import { catalogTypeLabel } from './recordTypes'
import { settleWhileActive } from './requests'

type DetailView =
  | { status: 'loading' }
  | { status: 'ready'; detail: CatalogDetail }
  | { status: 'error'; error: ApiError }

type Props = {
  api: ApiClient
  recordId: string
  /** 一覧や検索結果から開いた項目。詳細の取得を待つ間の見出しに使う（URL から直接開いたときは null） */
  item?: CatalogItem | null
  /** 所有チームによらず操作履歴を読める業務ロール（Curator・Admin）か */
  canViewHistory: boolean
  /** 公開停止・廃止を行える業務ロール（Curator）か。緊急時はカタログから見つけてすぐに止める */
  canManagePublication?: boolean
  onRelogin: () => void
  onClose: () => void
}

export function CatalogDetailView({
  api,
  recordId,
  item = null,
  canViewHistory,
  canManagePublication = false,
  onRelogin,
  onClose,
}: Props) {
  const [view, setView] = useState<DetailView>({ status: 'loading' })
  const heading = useRef<HTMLHeadingElement>(null)
  const isLoading = view.status === 'loading'
  useEffect(() => heading.current?.focus(), [])
  useEffect(() => {
    if (!isLoading) return
    return settleWhileActive(
      api.getCatalogDetail(recordId),
      'カタログの詳細を取得できませんでした。',
      (settled) =>
        setView(
          settled.ok
            ? { status: 'ready', detail: settled.value }
            : { status: 'error', error: settled.error },
        ),
    )
  }, [api, isLoading, recordId])

  const record = view.status === 'ready' ? view.detail : item
  const title = record ? record.displayName || record.name : recordId
  return (
    <section aria-labelledby="catalog-detail-heading">
      <DetailBreadcrumb
        parentTo={{ screen: 'catalog', recordId: null }}
        parentLabel="カタログ"
        current="詳細"
        onBack={onClose}
        backLabel="カタログに戻る"
        className="mb-5"
      />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
        <div className="xl:col-span-2 space-y-6">
          <section className={`${card} p-6`}>
            {view.status === 'ready' && <DetailBadges detail={view.detail} />}
            <h1
              id="catalog-detail-heading"
              ref={heading}
              tabIndex={-1}
              className={`${pageTitle} break-all outline-none`}
            >
              {title}
            </h1>
            {view.status === 'ready' ? (
              <>
                <p className="mt-1 text-xs font-mono text-slate-500 break-all">
                  name: {view.detail.name} · recordId: {view.detail.recordId}
                </p>
                <p className="mt-4 text-sm text-slate-700 leading-relaxed">{view.detail.description}</p>
                <p className={`mt-4 ${callout.info}`}>
                  公開承認は実行許可ではありません。利用前に、所有チームの利用条件を確認してください。
                </p>
              </>
            ) : null}
          </section>
          {view.status === 'loading' ? (
            <LoadingState>詳細を読み込んでいます…</LoadingState>
          ) : view.status === 'error' ? (
            <RequestErrorAlert
              error={view.error}
              onRelogin={onRelogin}
              onRetry={() => setView({ status: 'loading' })}
            />
          ) : (
            <DetailContents detail={view.detail} />
          )}
        </div>
        <aside className="space-y-4">
          {view.status === 'ready' && <DetailMetadata detail={view.detail} />}
          {(canManagePublication || canViewHistory) && (
            <section className={`${card} p-5 space-y-3`}>
              <h2 className="text-sm font-semibold text-slate-900">管理・監査操作</h2>
              {canManagePublication && (
                <RecordPublicationToggle api={api} recordId={recordId} onRelogin={onRelogin} />
              )}
              {canViewHistory && <RecordHistoryToggle api={api} recordId={recordId} onRelogin={onRelogin} />}
            </section>
          )}
        </aside>
      </div>
    </section>
  )
}

/** null は公開版スナップショットを取得できなかったこと、空文字は値が登録されていないことを表す */
function snapshotValue(value: string | null): string {
  return value === null ? '未取得' : value || '未登録'
}

function DetailBadges({ detail }: { detail: CatalogDetail }) {
  return (
    <div className="flex flex-wrap items-center gap-2 mb-2">
      <span className={badge.success}>公開承認済み</span>
      <span className={badge.neutral}>{catalogTypeLabel(detail.recordType)}</span>
      {detail.isA2A && <span className={badge.info}>A2A</span>}
    </div>
  )
}

function MetadataRow({ term, children, block = false }: { term: string; children: ReactNode; block?: boolean }) {
  return block ? (
    <div className="pt-2 border-t border-slate-100 first:border-t-0 first:pt-0">
      <dt className="text-slate-500 mb-1">{term}</dt>
      <dd className="text-slate-800 break-all leading-relaxed">{children}</dd>
    </div>
  ) : (
    <div className="flex justify-between gap-3">
      <dt className="text-slate-500 shrink-0">{term}</dt>
      <dd className="font-medium text-slate-900 text-right break-all">{children}</dd>
    </div>
  )
}

/** 右側の管理情報（公開中の版・所有チーム・連絡先など） */
function DetailMetadata({ detail }: { detail: CatalogDetail }) {
  return (
    <section className={`${card} p-5`}>
      <h2 className="text-sm font-semibold text-slate-900 pb-3 border-b border-slate-200">公開中の版・管理情報</h2>
      <dl className="mt-3 space-y-3 text-xs">
        <MetadataRow term="種別">
          {catalogTypeLabel(detail.recordType)}
          {detail.isA2A && '（A2A）'}
        </MetadataRow>
        <MetadataRow term="公開状態">公開承認済み</MetadataRow>
        <MetadataRow term="公開中の版">
          <span className="font-mono">{detail.recordVersion || '未登録'}</span>
        </MetadataRow>
        <MetadataRow term="所有チーム">{detail.ownerTeam || '未登録'}</MetadataRow>
        <MetadataRow term="データ分類">
          {detail.dataClassification ? DATA_CLASSIFICATION_LABELS[detail.dataClassification] : '未登録'}
        </MetadataRow>
        <MetadataRow term="連絡先" block>
          {snapshotValue(detail.contact)}
        </MetadataRow>
        <MetadataRow term="利用条件" block>
          {snapshotValue(detail.termsOfUse)}
        </MetadataRow>
        <MetadataRow term="接続先の参照" block>
          <span className="font-mono">{snapshotValue(detail.connectionReference)}</span>
        </MetadataRow>
      </dl>
    </section>
  )
}

function DetailContents({ detail }: { detail: CatalogDetail }) {
  return (
    <>
      <section className={`${card} p-6`}>
        <h2 className="text-base font-semibold text-slate-900 mb-3">関連参照</h2>
        {detail.relatedReferences === null ? (
          <p className="text-sm text-slate-600">未取得</p>
        ) : detail.relatedReferences.length === 0 ? (
          <p className="text-sm text-slate-600">関連参照はありません。</p>
        ) : (
          <ul className="divide-y divide-slate-100 border border-slate-200 rounded-lg overflow-hidden">
            {detail.relatedReferences.map(({ recordId, recordVersion }) => (
              <li key={`${recordId}:${recordVersion}`} className="px-4 py-3 flex items-center justify-between gap-3 text-xs">
                <span className="font-mono text-slate-700 break-all">
                  <code>{recordId}</code> 版 {recordVersion}
                </span>
                <RouteLink
                  to={{ screen: 'catalog', recordId }}
                  className="shrink-0 font-semibold text-blue-600 hover:underline"
                >
                  カタログで見る
                </RouteLink>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className={`${card} p-6`}>
        <div className="flex items-center justify-between gap-2 mb-3">
          <h2 className="text-base font-semibold text-slate-900">定義</h2>
          <span className="text-xs text-slate-500">テキストとして表示（実行しません）</span>
        </div>
        <pre className="p-4 rounded-lg bg-slate-900 text-slate-100 text-xs font-mono overflow-x-auto leading-relaxed whitespace-pre-wrap break-all">
          {JSON.stringify(detail.definition, null, 2)}
        </pre>
      </section>
      {detail.skillMarkdown && (
        <section className={`${card} p-6`}>
          <div className="flex items-center justify-between gap-2 mb-3">
            <h2 className="text-base font-semibold text-slate-900">SKILL.md</h2>
            <span className="text-xs text-slate-500">HTML やスクリプトは実行しません</span>
          </div>
          <pre
            className="p-4 rounded-lg bg-slate-50 border border-slate-200 text-slate-800 text-xs font-mono overflow-x-auto leading-relaxed whitespace-pre-wrap break-all"
            aria-label="SKILL.md"
          >
            {detail.skillMarkdown}
          </pre>
        </section>
      )}
    </>
  )
}
