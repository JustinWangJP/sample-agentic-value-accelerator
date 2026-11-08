import { useEffect, useState } from 'react'
import { ApiError, toApiError, type ApiClient } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import type { HistoryEntry, HistoryKind, HistoryState } from './api'

/** 操作と審査の種別の表示。バックエンドの OperationKind（domain/drafts.py）と揃える */
const KIND_LABELS: Record<HistoryKind, string> = {
  CREATE: '作成',
  UPDATE: '編集',
  SUBMIT: '申請',
  APPROVE: '公開承認',
  REJECT: '却下',
}

/** 操作の結果の表示。結果の追記記録がない操作は「結果確認中」（GLOSSARY「結果確認中」） */
const STATE_LABELS: Record<HistoryState, string> = {
  SUCCEEDED: '成功',
  FAILED: '失敗',
  IN_PROGRESS: '結果確認中',
  UNKNOWN: '不明',
}

function stateLabel(entry: HistoryEntry): string {
  const label = STATE_LABELS[entry.state]
  return entry.errorCode ? `${label}（${entry.errorCode}）` : label
}

/** 操作した主体。取り込み前から公開されていた版は、公開承認した主体が操作記録に残っていないため「不明」 */
function operatorLabel(entry: HistoryEntry): string {
  return entry.operator ? entry.operator.subject : '不明'
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

/** 履歴の表示を開閉するボタンと、開いたときの履歴。下書き編集・審査・カタログ詳細の各画面で使う */
export function RecordHistoryToggle(props: Props) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {open ? '履歴を閉じる' : '操作と審査の履歴を表示'}
      </button>
      {open && <RecordHistoryView {...props} />}
    </>
  )
}

/** Registry レコードごとの操作と審査の履歴（GET /api/records/{recordId}/history） */
export function RecordHistoryView({ api, recordId, onRelogin }: Props) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  useEffect(() => {
    let active = true
    api.getHistory(recordId).then(
      (history) => {
        if (active) setState({ kind: 'ready', entries: history.entries })
      },
      (failure: unknown) => {
        if (active)
          setState({
            kind: 'error',
            error: toApiError(failure, '操作履歴を取得できませんでした。'),
          })
      },
    )
    return () => {
      active = false
    }
  }, [api, recordId])

  return (
    <section className="history" aria-labelledby={`history-${recordId}`}>
      <h4 id={`history-${recordId}`}>操作と審査の履歴</h4>
      {state.kind === 'loading' ? (
        <p role="status">操作履歴を読み込んでいます…</p>
      ) : state.kind === 'error' ? (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} />
      ) : state.entries.length === 0 ? (
        <p>操作の記録はありません。</p>
      ) : (
        <ul className="history-entries">
          {state.entries.map((entry, index) => (
            <li key={entry.operationId ?? `unknown-${index}`}>
              <p>
                {KIND_LABELS[entry.kind]}・{stateLabel(entry)}
                {entry.recordVersion ? `・版 ${entry.recordVersion}` : ''}
              </p>
              <p>
                操作者 {operatorLabel(entry)}
                {entry.startedAt ? `・${entry.startedAt}` : ''}
              </p>
              <p>
                操作 ID {entry.operationId ?? 'なし'}
                {entry.awsRequestId ? `・AWS request ID ${entry.awsRequestId}` : ''}
              </p>
              {entry.targetHash && <p>対象のハッシュ {entry.targetHash}</p>}
              {entry.reason && <p>理由 {entry.reason}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
