import type { PublishedBase, RecordReference, SubmissionSnapshot } from './api'
import { card } from '../ui/classes'

// 審査詳細で、申請のスナップショットと公開中の版との差分を表示する部品

function formatReferences(references: RecordReference[]): string {
  return (
    references.map((reference) => `${reference.recordId} @ ${reference.recordVersion}`).join('、') ||
    'ありません'
  )
}

/** 申請のスナップショット（申請理由・連絡先・定義と metadata） */
export function SnapshotSection({ snapshot }: { snapshot: SubmissionSnapshot }) {
  return (
    <section aria-labelledby="review-snapshot-heading" className={`${card} p-6 space-y-3`}>
      <h2 id="review-snapshot-heading" className="text-base font-semibold text-slate-900">
        申請のスナップショット
      </h2>
      <p className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-lg p-3 break-all">
        <strong className="font-semibold text-slate-900">申請理由: </strong>
        {snapshot.reason}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs text-slate-700">
        <p className="break-all">申請者: {snapshot.submittedBy.subject} / 最終編集者: {snapshot.lastEditedBy.subject}</p>
        <p className="break-all">連絡先: {snapshot.contact || '未登録'} / 利用条件: {snapshot.termsOfUse || '未登録'}</p>
        <p className="break-all">接続先の参照: <span className="font-mono">{snapshot.connectionReference || '未登録'}</span></p>
        <p className="break-all">関連参照: <span className="font-mono">{formatReferences(snapshot.relatedReferences)}</span></p>
      </div>
      <details className="pt-1">
        <summary className="text-xs font-semibold text-slate-700 select-none">申請された定義と metadata を確認</summary>
        <pre className="mt-3 p-4 rounded-lg bg-slate-900 text-slate-100 text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all">
          {JSON.stringify({ record: snapshot.record, supplement: snapshot.supplement }, null, 2)}
        </pre>
      </details>
    </section>
  )
}

/** 差分で比べる項目。公開中の版（スナップショットがない項目は null）と申請された版 */
type DiffFields = {
  recordVersion: string
  contact: string | null
  termsOfUse: string | null
  connectionReference: string | null
  relatedReferences: RecordReference[]
  record: Record<string, unknown> | null
}

/** 定義と metadata のうち、差分で比べる項目（AWS のレコードの項目名と表示名） */
const RECORD_DIFF_FIELDS: [string, string][] = [
  ['displayName', '表示名'],
  ['description', '説明'],
  ['descriptors', '定義（descriptors）'],
  ['customMetadata', 'metadata'],
]

function recordValue(record: Record<string, unknown> | null, key: string): string {
  if (record === null) return '未取得'
  const value = record[key]
  if (value === undefined || value === null || value === '') return '未登録'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** 公開中の版と申請のスナップショットを比べる。ハッシュで対象を固定するため、差分は参考情報 */
export function DiffTable({ next, published }: { next: DiffFields; published: PublishedBase }) {
  const rows: [string, string, string][] = [
    ['版', published.recordVersion, next.recordVersion],
    ...RECORD_DIFF_FIELDS.map(([key, label]): [string, string, string] => [
      label,
      recordValue(published.record, key),
      recordValue(next.record, key),
    ]),
    ['連絡先', published.contact ?? '未登録', next.contact || '未登録'],
    ['利用条件', published.termsOfUse ?? '未登録', next.termsOfUse || '未登録'],
    ['接続先の参照', published.connectionReference ?? '未登録', next.connectionReference || '未登録'],
    ['関連参照', formatReferences(published.relatedReferences), formatReferences(next.relatedReferences)],
  ]
  return (
    <div className="overflow-x-auto">
      <table className="diff-table text-xs">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
            <th className="py-3 px-4 w-36 text-left">項目</th>
            <th className="py-3 px-4 text-left">公開中の版</th>
            <th className="py-3 px-4 text-left">申請された版</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {rows.map(([label, before, after]) => {
            const changed = before !== after
            return (
              <tr key={label} className={changed ? 'bg-amber-50/40' : undefined}>
                <th className="py-3 px-4 font-semibold text-slate-700 text-left">{label}</th>
                <td className="py-3 px-4 text-slate-600">{before}</td>
                <td className={changed ? 'py-3 px-4 font-medium text-slate-900' : 'py-3 px-4 text-slate-700'}>{after}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
