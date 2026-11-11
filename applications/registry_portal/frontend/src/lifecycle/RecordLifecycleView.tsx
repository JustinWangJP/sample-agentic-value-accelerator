import { useState, type FormEvent } from 'react'
import type { ApiClient } from '../api/client'
import { RECORD_ID_LENGTH, RECORD_ID_PATTERN } from '../drafts/api'
import { RecordLifecyclePanel } from './RecordLifecyclePanel'

type Props = { api: ApiClient; roles: string[]; onRelogin: () => void }

/**
 * Curator がレコード ID を指定して公開管理を開く入口。公開停止中などカタログに出ないレコードも扱える。
 * 公開中のレコードはカタログの詳細から、廃止依頼は審査待ちの一覧から、同じ公開管理を開ける
 */
export function RecordLifecycleView({ api, roles, onRelogin }: Props) {
  const [recordId, setRecordId] = useState('')
  const [target, setTarget] = useState<string | null>(null)

  if (!roles.includes('Curator')) return null

  function open(event: FormEvent) {
    event.preventDefault()
    setTarget(recordId)
  }

  return (
    <section aria-labelledby="lifecycle-heading">
      <h2 id="lifecycle-heading">公開管理</h2>
      <p className="note">
        レコード ID を指定して、公開停止・廃止・廃止依頼の見送りを行います。公開停止中のレコードは
        カタログに表示されないため、ここから開いてください。
      </p>
      <form onSubmit={open}>
        <label>
          Registry レコード ID
          <input
            name="lifecycleRecordId"
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
        <RecordLifecyclePanel key={target} api={api} recordId={target} onRelogin={onRelogin} />
      )}
    </section>
  )
}
