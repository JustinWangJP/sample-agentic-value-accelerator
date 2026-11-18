import { useState, type FormEvent } from 'react'
import type { ApiClient } from '../api/client'
import { RECORD_ID_LENGTH, RECORD_ID_PATTERN } from '../drafts/api'
import { RecordPublicationPanel } from './RecordPublicationPanel'
import { RecordPublicationSearch } from './RecordPublicationSearch'

type Props = { api: ApiClient; roles: string[]; onRelogin: () => void }

/**
 * Curator が公開管理を開く入口。管理レコードの検索（名前の一部と状態）とレコード ID の指定の2つがあり、
 * 公開停止中などカタログに出ないレコードも扱える。公開中のレコードはカタログの詳細から、廃止依頼は
 * 審査待ちの一覧から、同じ公開管理を開ける
 */
export function RecordPublicationView({ api, roles, onRelogin }: Props) {
  const [recordId, setRecordId] = useState('')
  const [target, setTarget] = useState<string | null>(null)

  if (!roles.includes('Curator')) return null

  function open(event: FormEvent) {
    event.preventDefault()
    setTarget(recordId)
  }

  return (
    <section aria-labelledby="publication-heading">
      <h2 id="publication-heading">公開管理</h2>
      <p className="note">
        公開停止・廃止・廃止依頼の見送りを行います。公開停止中のレコードはカタログに表示されないため、
        検索で探すか、レコード ID を指定して開いてください。
      </p>
      <RecordPublicationSearch api={api} onRelogin={onRelogin} />
      <form onSubmit={open}>
        <label>
          Registry レコード ID
          <input
            name="publicationRecordId"
            value={recordId}
            pattern={RECORD_ID_PATTERN}
            maxLength={RECORD_ID_LENGTH}
            onChange={(event) => setRecordId(event.target.value)}
          />
        </label>
        <button type="submit" disabled={recordId.length !== RECORD_ID_LENGTH}>
          公開管理を開く
        </button>
      </form>
      {target !== null && (
        <RecordPublicationPanel key={target} api={api} recordId={target} onRelogin={onRelogin} />
      )}
    </section>
  )
}
