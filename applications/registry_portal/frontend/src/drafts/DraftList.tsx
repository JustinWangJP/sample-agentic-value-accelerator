import { useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { CursorPager } from '../components/NavigationControls'
import { OperationCheck } from '../components/OperationCheck'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { badge, button, card } from '../ui/classes'
import {
  DEFAULT_DRAFT_ENVIRONMENT,
  isEditableStatus,
  recordStatusLabel,
  type DraftPage,
} from './api'

type PageState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: DraftPage }
  | { kind: 'error'; error: ApiError }

const DRAFT_COLUMNS = 'md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.2fr)_6rem]'

/** 所属チームの下書き対象の一覧。開くたびに 1 ページ目から読み込む */
export function DraftList({
  api,
  teams,
  onCreate,
  onEdit,
  onRelogin,
}: {
  api: ApiClient
  teams: string[]
  onCreate: () => void
  onEdit: (recordId: string) => void
  onRelogin: () => void
}) {
  const [page, setPage] = useState<PageState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    api.listDrafts(token).then(
      (result) => {
        if (active) setPage({ kind: 'ready', page: result })
      },
      (failure: unknown) => {
        if (active)
          setPage({
            kind: 'error',
            error: toApiError(failure, '下書き一覧を取得できませんでした。'),
          })
      },
    )
    return () => {
      active = false
    }
  }, [api, token, revision])

  function reload() {
    setPage({ kind: 'loading' })
    setToken(undefined)
    setRevision((value) => value + 1)
  }

  return (
    <section aria-labelledby="drafts-heading">
      <PageHeader
        headingId="drafts-heading"
        title="下書きの管理"
        description={
          <p>
            所属チーム（<span className="font-medium text-slate-800">{teams.join('、')}</span>）の Agent（custom・A2A）・MCP
            サーバー・Skill を登録します。environment は {DEFAULT_DRAFT_ENVIRONMENT} 固定です。保存しても公開されません。
          </p>
        }
        actions={
          <button type="button" className={button.primary} onClick={onCreate}>
            下書きを作成
          </button>
        }
      />
      {page.kind === 'loading' ? (
        <LoadingState>一覧を読み込んでいます…</LoadingState>
      ) : page.kind === 'error' ? (
        <RequestErrorAlert error={page.error} onRelogin={onRelogin} onRetry={reload} retryLabel="一覧を再読み込み" />
      ) : (
        <div className="space-y-4">
          {page.page.items.length === 0 && (
            <EmptyState hint={page.page.nextToken ? undefined : '「下書きを作成」から Agent・MCP サーバー・Skill を作成できます。'}>
              {page.page.nextToken
                ? 'このページには所属チームの下書きがありません。次のページを確認してください。'
                : '所属チームの下書き対象はありません。'}
            </EmptyState>
          )}
          {page.page.items.length > 0 && (
            <div className={`${card} overflow-hidden`}>
              <div className={`hidden md:grid ${DRAFT_COLUMNS} bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600`} aria-hidden="true">
                <span className="py-3 px-4">レコード名 / 種別・所有チーム</span>
                <span className="py-3 px-4">版（最新 / 公開中）</span>
                <span className="py-3 px-4">状態</span>
                <span className="py-3 px-4 text-right">操作</span>
              </div>
              <ul className="divide-y divide-slate-200 text-sm">
                {page.page.items.map((item) => (
                  <li key={item.recordId} className="hover:bg-slate-50/70">
                    <div className={`grid grid-cols-1 ${DRAFT_COLUMNS} items-start`}>
                      <div className="py-3.5 px-4 min-w-0">
                        <h3 className="font-semibold text-slate-900 break-all">{item.displayName}</h3>
                        <p className="text-xs text-slate-500 break-all">
                          {RECORD_TYPE_LABELS[item.recordType]}・<span className="font-mono">{item.name}</span>・{item.ownerTeam}
                        </p>
                      </div>
                      <div className="px-4 md:py-3.5 text-xs font-mono">
                        <p className="text-slate-900 font-semibold">版 {item.recordVersion}</p>
                        <p className={item.publishedVersion ? 'text-emerald-700' : 'text-slate-400'}>
                          {item.publishedVersion ? `公開中 ${item.publishedVersion}` : '公開版なし'}
                        </p>
                      </div>
                      <div className="px-4 md:py-3.5 text-xs space-y-1">
                        <p>
                          <span className={item.hasDraft ? badge.neutral : badge.code}>
                            {item.hasDraft ? '下書きあり' : '下書きなし'}
                          </span>
                        </p>
                        <p className="text-slate-600">
                          AWS の処理状態 {recordStatusLabel(item.status)}
                          {item.operationId && ' / 結果確認中'}
                        </p>
                      </div>
                      <div className="py-3.5 px-4 md:text-right">
                        <button
                          type="button"
                          className={button.small}
                          disabled={item.operationId !== null || !isEditableStatus(item.status)}
                          onClick={() => onEdit(item.recordId)}
                        >
                          編集
                        </button>
                      </div>
                    </div>
                    {item.operationId && (
                      <div className="px-4 pb-4">
                        <OperationCheck
                          operationId={item.operationId}
                          load={(id) => api.getDraftOperation(id)}
                          onDone={reload}
                          onRelogin={onRelogin}
                        />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <CursorPager
            ariaLabel="下書きのページ"
            hasPrevious={token !== undefined}
            hasNext={page.page.nextToken !== null}
            onFirst={() => {
              setPage({ kind: 'loading' })
              setToken(undefined)
            }}
            onNext={() => {
              if (page.page.nextToken) {
                setToken(page.page.nextToken)
                setPage({ kind: 'loading' })
              }
            }}
          />
        </div>
      )}
    </section>
  )
}
