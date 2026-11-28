import { useState, type FormEvent } from 'react'
import { PageHeader } from '../components/StatePanel'
import { RECORD_ID_LENGTH, RECORD_ID_PATTERN } from '../drafts/api'
import { button, card, fieldLabel } from '../ui/classes'
import { RecordPublicationPanel } from './RecordPublicationPanel'
import { RecordPublicationSearch } from './RecordPublicationSearch'
import { canOpenScreen } from '../routing/routes'
import type { ScreenProps } from '../shell/screenProps'

type Props = ScreenProps

/**
 * Curator が公開管理を開く入口。管理レコードの検索（名前の一部と状態）とレコード ID の指定の2つがあり、
 * 公開停止中などカタログに出ないレコードも扱える。公開中のレコードはカタログの詳細から、廃止依頼は
 * 審査待ちの一覧から、同じ公開管理を開ける
 */
export function RecordPublicationView({ api, roles, onRelogin }: Props) {
  const [recordId, setRecordId] = useState('')
  const [target, setTarget] = useState<string | null>(null)

  if (!canOpenScreen('publication', roles)) return null

  function open(event: FormEvent) {
    event.preventDefault()
    setTarget(recordId)
  }

  return (
    <section aria-labelledby="publication-heading" className="space-y-6">
      <PageHeader
        headingId="publication-heading"
        title="公開管理"
        description={
          <p>
            公開停止・廃止・廃止依頼の見送りを行います。公開停止中のレコードはカタログに表示されないため、
            検索で探すか、レコード ID を指定して開いてください。
          </p>
        }
      />
      <RecordPublicationSearch
        api={api}
        onRelogin={onRelogin}
        aside={
          <form onSubmit={open} className={`${card} p-5 space-y-3`}>
            <h2 className="text-sm font-semibold text-slate-900">レコード ID で直接照会</h2>
            <div className="flex items-end gap-2">
              <label className={`${fieldLabel} flex-1`}>
                Registry レコード ID
                <input
                  name="publicationRecordId"
                  className="font-mono"
                  value={recordId}
                  pattern={RECORD_ID_PATTERN}
                  maxLength={RECORD_ID_LENGTH}
                  placeholder={`${RECORD_ID_LENGTH} 桁のレコード ID`}
                  onChange={(event) => setRecordId(event.target.value)}
                />
              </label>
              <button type="submit" className={`${button.secondary} shrink-0`} disabled={recordId.length !== RECORD_ID_LENGTH}>
                公開管理を開く
              </button>
            </div>
          </form>
        }
      />
      {target !== null && (
        <RecordPublicationPanel key={target} api={api} recordId={target} onRelogin={onRelogin} />
      )}
    </section>
  )
}
