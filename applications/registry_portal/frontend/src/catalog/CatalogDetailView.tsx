import { useEffect, useRef, useState } from 'react'
import type { ApiClient, ApiError, CatalogDetail, CatalogItem } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import { RecordPublicationToggle } from '../publication/RecordPublicationPanel'
import { catalogTypeLabel } from './recordTypes'
import { settleWhileActive } from './requests'

type DetailView =
  | { status: 'loading' }
  | { status: 'ready'; detail: CatalogDetail }
  | { status: 'error'; error: ApiError }

type Props = {
  api: ApiClient
  item: CatalogItem
  /** 所有チームによらず操作履歴を読める業務ロール（Curator・Admin）か */
  canViewHistory: boolean
  /** 公開停止・廃止を行える業務ロール（Curator）か。緊急時はカタログから見つけてすぐに止める */
  canManagePublication?: boolean
  onRelogin: () => void
  onClose: () => void
}

export function CatalogDetailView({
  api,
  item,
  canViewHistory,
  canManagePublication = false,
  onRelogin,
  onClose,
}: Props) {
  const [view, setView] = useState<DetailView>({ status: 'loading' })
  const heading = useRef<HTMLHeadingElement>(null)
  const { recordId } = item
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
  return (
    <section aria-labelledby="catalog-detail-heading" className="catalog">
      <button type="button" onClick={onClose}>
        カタログに戻る
      </button>
      <h2 id="catalog-detail-heading" ref={heading} tabIndex={-1}>
        {record.displayName || record.name}
      </h2>
      {view.status === 'loading' ? (
        <p role="status">詳細を読み込んでいます…</p>
      ) : view.status === 'error' ? (
        <RequestErrorAlert
          error={view.error}
          onRelogin={onRelogin}
          onRetry={() => setView({ status: 'loading' })}
        />
      ) : (
        <DetailContents detail={view.detail} />
      )}
      {canManagePublication && (
        <RecordPublicationToggle api={api} recordId={recordId} onRelogin={onRelogin} />
      )}
      {canViewHistory && <RecordHistoryToggle api={api} recordId={recordId} onRelogin={onRelogin} />}
    </section>
  )
}

/** null は公開版スナップショットを取得できなかったこと、空文字は値が登録されていないことを表す */
function snapshotValue(value: string | null): string {
  return value === null ? '未取得' : value || '未登録'
}

function DetailContents({ detail }: { detail: CatalogDetail }) {
  return (
    <>
      <p>{detail.description}</p>
      <p className="note">
        公開承認は実行許可ではありません。利用前に、所有チームの利用条件を確認してください。
      </p>
      <dl>
        <dt>種別</dt>
        <dd>
          {catalogTypeLabel(detail.recordType)}
          {detail.isA2A && '（A2A）'}
        </dd>
        <dt>公開状態</dt>
        <dd>公開承認済み</dd>
        <dt>公開中の版</dt>
        <dd>{detail.recordVersion || '未登録'}</dd>
        <dt>所有チーム</dt>
        <dd>{detail.ownerTeam || '未登録'}</dd>
        <dt>連絡先</dt>
        <dd>{snapshotValue(detail.contact)}</dd>
        <dt>利用条件</dt>
        <dd>{snapshotValue(detail.termsOfUse)}</dd>
        <dt>データ分類</dt>
        <dd>{detail.dataClassification ?? '未登録'}</dd>
        <dt>接続先の参照</dt>
        <dd>{snapshotValue(detail.connectionReference)}</dd>
      </dl>
      <h3>関連参照</h3>
      {detail.relatedReferences === null ? (
        <p>未取得</p>
      ) : detail.relatedReferences.length === 0 ? (
        <p>関連参照はありません。</p>
      ) : (
        <ul>
          {detail.relatedReferences.map(({ recordId, recordVersion }) => (
            <li key={`${recordId}:${recordVersion}`}>
              <code>{recordId}</code> 版 {recordVersion}
            </li>
          ))}
        </ul>
      )}
      <h3>定義</h3>
      <pre className="catalog-definition">{JSON.stringify(detail.definition, null, 2)}</pre>
      {detail.skillMarkdown && (
        <>
          <h3>SKILL.md</h3>
          <pre className="catalog-definition" aria-label="SKILL.md">{detail.skillMarkdown}</pre>
        </>
      )}
    </>
  )
}
