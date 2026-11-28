import { useEffect, useState, type ReactNode } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { CursorPager } from '../components/NavigationControls'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { recordStatusLabel } from '../drafts/api'
import { elapsedDays } from '../elapsedDays'
import { RecordPublicationToggle } from '../publication/RecordPublicationPanel'
import { badge, button, callout, card, listHeader } from '../ui/classes'
import type { DeprecationRequestSummary, PendingPage, SubmissionSummary } from './api'

type ListState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: PendingPage }
  | { kind: 'error'; error: ApiError }

/** 一覧の申請理由。申請がない場合と、申請はあるはずなのに読めない場合を区別する */
function submissionLine(item: SubmissionSummary): string {
  if (item.submissionState === 'MISSING')
    return '申請スナップショットを取得できません（管理者に連絡してください）'
  if (item.submissionState === 'NONE' || item.reason === null) return 'ポータル経由の申請なし'
  return item.reason
}

/** 廃止依頼の項目の状況。公開停止済みなら、廃止するか見送るかの判断待ち */
function requestProgress(item: DeprecationRequestSummary): string {
  const days = elapsedDays(item.requestedAt)
  const elapsed = days === null ? '' : `・${days} 日経過`
  return item.suspended ? `公開停止済み・廃止の判断待ち${elapsed}` : `未対応${elapsed}`
}

const REVIEW_COLUMNS = 'md:grid-cols-[6.5rem_minmax(0,2fr)_6rem_minmax(0,2.5fr)_9.5rem_7rem]'

/** 審査待ちの一覧。開くたびに 1 ページ目から読み込む */
export function ReviewList({
  api,
  onOpen,
  onRelogin,
}: {
  api: ApiClient
  onOpen: (recordId: string) => void
  onRelogin: () => void
}) {
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    api.listReviews(token).then(
      (result) => {
        if (active) setList({ kind: 'ready', page: result })
      },
      (failure: unknown) => {
        if (active)
          setList({
            kind: 'error',
            error: toApiError(failure, '審査待ちの一覧を取得できませんでした。'),
          })
      },
    )
    return () => {
      active = false
    }
  }, [api, token, revision])

  function reload() {
    setList({ kind: 'loading' })
    setToken(undefined)
    setRevision((value) => value + 1)
  }

  return (
    <section aria-labelledby="reviews-heading">
      <PageHeader
        headingId="reviews-heading"
        title="審査待ち"
        description={
          <>
            <p>
              AWS の承認待ち（PENDING_APPROVAL）を正本として表示します。申請のスナップショットを確認してから
              公開承認または却下を行ってください。公開承認は実行の許可ではありません。
            </p>
            <p>
              未解決の廃止依頼は、最初のページに古い順で表示します。公開停止で止めてから、廃止するか見送るかを判断できます。
            </p>
          </>
        }
      />
      {list.kind === 'loading' ? (
        <LoadingState>一覧を読み込んでいます…</LoadingState>
      ) : list.kind === 'error' ? (
        <RequestErrorAlert error={list.error} onRelogin={onRelogin} onRetry={reload} retryLabel="一覧を再読み込み" />
      ) : (
        <div className="space-y-4">
          {list.page.items.length === 0 && (
            <EmptyState hint={list.page.nextToken ? undefined : '審査が必要な申請と廃止依頼はすべて処理済みです。'}>
              {list.page.nextToken ? 'このページに審査待ちはありません。' : '審査待ちはありません。'}
            </EmptyState>
          )}
          {list.page.items.length > 0 && (
            <div className={`${card} overflow-hidden`}>
              <div
                className={`${listHeader} ${REVIEW_COLUMNS}`}
                aria-hidden="true"
              >
                <span className="py-3 px-4">区分</span>
                <span className="py-3 px-4">レコード名 / 所有チーム</span>
                <span className="py-3 px-4">版</span>
                <span className="py-3 px-4">理由・申請者 / 経過</span>
                <span className="py-3 px-4">状態</span>
                <span className="py-3 px-4 text-right">操作</span>
              </div>
              <ul className="divide-y divide-slate-200 text-sm">
                {list.page.items.map((item) =>
                  item.reviewKind === 'DEPRECATION_REQUEST' ? (
                    <ReviewRow
                      key={`request-${item.recordId}`}
                      item={item}
                      kindLabel="廃止依頼"
                      kindClassName={badge.danger}
                      statusClassName={badge.neutral}
                      reason={
                        <>
                          <p>
                            廃止依頼: {item.reason}（依頼者 {item.requestedBy.subject}）
                          </p>
                          <p className="mt-0.5 text-rose-700 font-medium">{requestProgress(item)}</p>
                        </>
                      }
                    >
                      <div className="px-4 pb-4 space-y-2">
                        {!item.managed && (
                          <p className={callout.warning}>ポータルで管理されていないため、ここでは対応できません。</p>
                        )}
                        <RecordPublicationToggle
                          api={api}
                          recordId={item.recordId}
                          onRelogin={onRelogin}
                          label="対応する"
                        />
                      </div>
                    </ReviewRow>
                  ) : (
                    <ReviewRow
                      key={item.recordId}
                      item={item}
                      kindLabel="申請"
                      kindClassName={badge.warning}
                      statusClassName={badge.warning}
                      reason={
                        <p>
                          申請理由: {submissionLine(item)}
                          {item.submittedBy && `（申請者 ${item.submittedBy.subject}）`}
                        </p>
                      }
                      action={
                        <button
                          type="button"
                          className={button.smallPrimary}
                          disabled={item.operationId !== null || !item.snapshotHash}
                          onClick={() => onOpen(item.recordId)}
                        >
                          審査する
                        </button>
                      }
                    >
                      {!item.managed && (
                        <p className={`mx-4 mb-4 ${callout.warning}`}>ポータルで管理されていないため、ここでは決裁できません。</p>
                      )}
                    </ReviewRow>
                  ),
                )}
              </ul>
            </div>
          )}
          <CursorPager
            ariaLabel="審査待ちのページ"
            hasPrevious={token !== undefined}
            hasNext={list.page.nextToken !== null}
            onFirst={() => {
              setList({ kind: 'loading' })
              setToken(undefined)
            }}
            onNext={() => {
              if (list.page.nextToken) {
                setToken(list.page.nextToken)
                setList({ kind: 'loading' })
              }
            }}
          />
        </div>
      )}
    </section>
  )
}

/**
 * 審査待ちの 1 行。申請と廃止依頼で、区分・理由・操作の中身だけを変え、列の並びは共有する。
 * children は行の下に置く補足（管理外の警告・公開管理）
 */
function ReviewRow({
  item,
  kindLabel,
  kindClassName,
  statusClassName,
  reason,
  action,
  children,
}: {
  item: SubmissionSummary | DeprecationRequestSummary
  kindLabel: string
  kindClassName: string
  statusClassName: string
  reason: ReactNode
  action?: ReactNode
  children?: ReactNode
}) {
  return (
    <li className="hover:bg-slate-50/70">
      <div className={`grid grid-cols-1 ${REVIEW_COLUMNS} items-start`}>
        <p className="pt-3.5 px-4 md:py-3.5">
          <span className={kindClassName}>{kindLabel}</span>
        </p>
        <ReviewRecordCell item={item} />
        <p className="px-4 md:py-3.5 text-xs font-mono font-semibold text-slate-900">版 {item.recordVersion}</p>
        <div className="px-4 md:py-3.5 text-xs text-slate-600 break-all">{reason}</div>
        <p className="px-4 md:py-3.5 text-xs">
          <span className={statusClassName}>
            状態 {recordStatusLabel(item.status)}
            {item.operationId && ' / 結果確認中'}
          </span>
        </p>
        <div className="py-3.5 px-4 md:text-right">{action}</div>
      </div>
      {children}
    </li>
  )
}

/** 一覧の名前・種別・所有チームの列 */
function ReviewRecordCell({ item }: { item: SubmissionSummary | DeprecationRequestSummary }) {
  return (
    <div className="px-4 md:py-3.5 min-w-0">
      <h3 className="font-semibold text-slate-900 break-all">{item.displayName}</h3>
      <p className="text-xs text-slate-500 break-all">
        {RECORD_TYPE_LABELS[item.recordType]}・<span className="font-mono">{item.name}</span>・
        <strong>{item.ownerTeam ?? '所有チーム不明'}</strong>
      </p>
    </div>
  )
}
