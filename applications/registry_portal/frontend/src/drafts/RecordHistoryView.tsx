import { useEffect, useState } from 'react'
import type { ApiClient, ApiError } from '../api/client'
import { settleWhileActive } from '../catalog/requests'
import { Drawer } from '../components/Overlay'
import { RecoveryDetails } from '../components/RecoveryDetails'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState } from '../components/StatePanel'
import { button } from '../ui/classes'
import { KIND_LABELS, STATE_LABELS, type HistoryEntry, type HistoryState } from './api'

function stateLabel(entry: HistoryEntry): string {
  const label = STATE_LABELS[entry.state]
  return entry.errorCode ? `${label}（${entry.errorCode}）` : label
}

/**
 * 操作した主体（issuer と subject の組）。
 * 取り込み前から公開されていた版は、公開承認した主体が操作記録に残っていないため「不明」
 */
function principalLabel(entry: HistoryEntry): string {
  return entry.principal ? `${entry.principal.subject}（${entry.principal.issuer}）` : '不明'
}

/** 時系列の点と見出しの色。結果（成功・失敗・結果確認中・不明）で塗り分ける */
const STATE_CLASSES: Record<HistoryState, { dot: string; label: string }> = {
  SUCCEEDED: { dot: 'bg-emerald-500', label: 'text-emerald-700' },
  FAILED: { dot: 'bg-rose-500', label: 'text-rose-800' },
  VERIFYING: { dot: 'bg-amber-500', label: 'text-amber-800' },
  UNKNOWN: { dot: 'bg-slate-400', label: 'text-slate-700' },
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; entries: HistoryEntry[] }
  | { kind: 'error'; error: ApiError }

type Props = {
  api: ApiClient
  recordId: string
  onRelogin: () => void
}

/** 履歴のドロワーを開閉するボタン。下書き編集・審査・カタログ詳細の各画面で使う */
export function RecordHistoryToggle(props: Props) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`${button.small} w-full`}
      >
        {open ? '履歴を閉じる' : '操作と審査の履歴を表示'}
      </button>
      {open && (
        <Drawer title="操作と審査の履歴" subtitle={`recordId: ${props.recordId}`} onClose={() => setOpen(false)}>
          <RecordHistoryView {...props} />
        </Drawer>
      )}
    </>
  )
}

/** Registry レコードごとの操作と審査の履歴（GET /api/records/{recordId}/history） */
export function RecordHistoryView({ api, recordId, onRelogin }: Props) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  useEffect(
    () =>
      settleWhileActive(api.getHistory(recordId), '操作履歴を取得できませんでした。', (result) =>
        setState(
          result.ok
            ? { kind: 'ready', entries: result.value.entries }
            : { kind: 'error', error: result.error },
        ),
      ),
    [api, recordId],
  )

  return (
    <section aria-labelledby={`history-${recordId}`}>
      <h3 id={`history-${recordId}`} className="sr-only">
        操作と審査の履歴
      </h3>
      {state.kind === 'loading' ? (
        <LoadingState>操作履歴を読み込んでいます…</LoadingState>
      ) : state.kind === 'error' ? (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} />
      ) : state.entries.length === 0 ? (
        <EmptyState>操作の記録はありません。</EmptyState>
      ) : (
        <ol className="relative border-l border-slate-200 ml-2 space-y-5">
          {state.entries.map((entry, index) => {
            const classes = STATE_CLASSES[entry.state]
            return (
              <li key={entry.operationId ?? `no-id-${index}`} className="pl-5 relative text-xs text-slate-700">
                <span className={`absolute -left-1.5 top-1 w-3 h-3 rounded-full ${classes.dot} ring-4 ring-white`} aria-hidden="true" />
                <p className={`font-semibold ${classes.label}`}>
                  {KIND_LABELS[entry.kind]}・{stateLabel(entry)}
                  {entry.recordVersion ? `・版 ${entry.recordVersion}` : ''}
                </p>
                <p className="mt-0.5 text-slate-500 break-all">
                  主体 {principalLabel(entry)}
                  {entry.startedAt ? `・${entry.startedAt}` : ''}
                </p>
                {entry.withheld ? (
                  <p className="mt-1">結果確認中の決裁の内容は、決裁した Curator 本人にだけ表示します。</p>
                ) : (
                  <>
                    {entry.reason && <p className="mt-1 text-slate-800">理由 {entry.reason}</p>}
                    <div className="mt-2 font-mono text-[11px] text-slate-500 space-y-0.5 bg-slate-50 p-2.5 rounded border border-slate-200 break-all">
                      <p>
                        操作 ID {entry.operationId ?? 'なし'}
                        {entry.awsRequestId ? `・AWS request ID ${entry.awsRequestId}` : ''}
                      </p>
                      {entry.targetHash && <p>対象のハッシュ {entry.targetHash}</p>}
                    </div>
                    {entry.recovery && (
                      <div className="mt-1.5 bg-indigo-50 border border-indigo-200 rounded p-2.5 text-indigo-900">
                        <RecoveryDetails recovery={entry.recovery} />
                      </div>
                    )}
                  </>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
