import { useEffect, useState } from 'react'
import { ApiError, toApiError, type ApiClient } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import type { HistoryEntry } from './api'

/** 操作と審査の種別の表示。バックエンドの OperationKind（domain/drafts.py）と揃える */
const KIND_LABELS: Record<HistoryEntry['kind'], string> = {
  CREATE: '作成',
  UPDATE: '編集',
  SUBMIT: '申請',
  APPROVE: '公開承認',
  REJECT: '却下',
}

/** 操作の結果の表示 */
function stateLabel(entry: HistoryEntry): string {
  if (entry.state === 'UNKNOWN') return '不明'
  if (entry.state === 'SUCCEEDED') return '成功'
  if (entry.state === 'FAILED') return `失敗（${entry.errorCode ?? ''}）`
  return '進行中'
}

/** 操作者。取り込み前から公開されていた項目は、承認者が操作記録に残っていないため「不明」 */
function operatorLabel(entry: HistoryEntry): string {
  return entry.operator ? entry.operator.subject : '不明'
}

type HistoryState =
  | { kind: 'loading' }
  | { kind: 'ready'; entries: HistoryEntry[] }
  | { kind: 'error'; error: ApiError }

/** Registry レコードごとの操作と審査の履歴（GET /api/records/{recordId}/history） */
export function RecordHistoryView({
  api,
  recordId,
  onRelogin,
}: {
  api: ApiClient
  recordId: string
  onRelogin: () => void
}) {
  const [state, setState] = useState<HistoryState>({ kind: 'loading' })

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
