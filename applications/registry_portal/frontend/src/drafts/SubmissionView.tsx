import { useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { AGENT_RECORD_TYPE, RECORD_TYPE_LABELS } from '../api/recordType'
import {
  isUncertainWriteError,
  RECORD_ID_LENGTH,
  RECORD_ID_PATTERN,
  RELATED_REFERENCES_MAX,
  settlesIdempotencyKey,
  SUBMISSION_REASON_MAX_LENGTH,
  type DraftDetail,
  type DraftOperation,
  type DraftSubmission,
} from './api'
import { OperationCheck } from '../components/OperationCheck'

type SubmissionState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'result'; operation: DraftOperation }

export function SubmissionView({
  api,
  detail,
  onBack,
  onRelogin,
}: {
  api: ApiClient
  detail: DraftDetail
  onBack: () => void
  onRelogin: () => void
}) {
  const [reason, setReason] = useState('')
  const [references, setReferences] = useState<DraftSubmission['relatedReferences']>([])
  const [state, setState] = useState<SubmissionState>({ kind: 'idle' })
  // 申請フォームごとの Idempotency-Key。結果が不明な間は同じキーを保ち、確定した失敗の後は作り直す
  const [submitKey, setSubmitKey] = useState(() => crypto.randomUUID())
  const uncertain = state.kind === 'error' && isUncertainWriteError(state.error)
  const isAgent = detail.draft.recordType === AGENT_RECORD_TYPE
  const locked =
    state.kind === 'submitting' ||
    uncertain ||
    (state.kind === 'result' && state.operation.operationState !== 'FAILED')

  async function submit() {
    if (locked || !reason.trim()) return
    setState({ kind: 'submitting' })
    try {
      const operation = await api.submitDraft(
        detail.recordId,
        {
          expectedContentHash: detail.contentHash,
          reason: reason.trim(),
          relatedReferences: isAgent ? references : [],
        },
        submitKey,
      )
      if (operation.operationState === 'FAILED') setSubmitKey(crypto.randomUUID())
      setState({ kind: 'result', operation })
    } catch (failure) {
      const error = toApiError(failure, '申請できませんでした。')
      if (settlesIdempotencyKey(error)) setSubmitKey(crypto.randomUUID())
      setState({ kind: 'error', error })
    }
  }

  return (
    <section aria-labelledby="submission-heading">
      <h3 id="submission-heading">下書きを申請</h3>
      <p>{detail.draft.displayName}（{RECORD_TYPE_LABELS[detail.draft.recordType]}）</p>
      <p>版 {detail.draft.recordVersion} / 所有チーム {detail.draft.ownerTeam}</p>
      <p>
        {detail.publishedVersion ? `公開中の版 ${detail.publishedVersion}` : '公開版なし'}。
        保存済みの内容を審査に提出します。申請しても公開されません。
      </p>
      <details>
        <summary>申請する保存済みの内容を確認</summary>
        <pre>{JSON.stringify(detail.draft, null, 2)}</pre>
      </details>
      <form onSubmit={(event) => { event.preventDefault(); void submit() }}>
        <fieldset disabled={locked}>
          <label>
            申請理由
            <textarea name="reason" required maxLength={SUBMISSION_REASON_MAX_LENGTH} rows={4} value={reason}
              onChange={(event) => setReason(event.target.value)} />
          </label>
          {isAgent && (
            <fieldset>
              <legend>関連参照（任意、最大{RELATED_REFERENCES_MAX}件）</legend>
              <p>公開済みのカタログ項目のレコード ID と版を指定してください。</p>
              {references.map((reference, index) => (
                <div key={index}>
                  <label>
                    レコード ID {index + 1}
                    <input name={`referenceId-${index}`} required pattern={RECORD_ID_PATTERN}
                      maxLength={RECORD_ID_LENGTH} value={reference.recordId}
                      onChange={(event) => setReferences(references.map((item, i) =>
                        i === index ? { ...item, recordId: event.target.value } : item))} />
                  </label>
                  <label>
                    レコードの版 {index + 1}
                    <input name={`referenceVersion-${index}`} required value={reference.recordVersion}
                      onChange={(event) => setReferences(references.map((item, i) =>
                        i === index ? { ...item, recordVersion: event.target.value } : item))} />
                  </label>
                  <button type="button" onClick={() => setReferences(references.filter((_, i) => i !== index))}>
                    関連参照 {index + 1} を削除
                  </button>
                </div>
              ))}
              <button type="button" disabled={references.length >= RELATED_REFERENCES_MAX}
                onClick={() => setReferences([...references, { recordId: '', recordVersion: '' }])}>
                関連参照を追加
              </button>
            </fieldset>
          )}
          <button type="submit" disabled={locked || !reason.trim()}>申請する</button>
        </fieldset>
      </form>
      {state.kind === 'submitting' && <p role="status">申請しています…</p>}
      {state.kind === 'error' && (
        <>
          <RequestErrorAlert error={state.error} onRelogin={onRelogin} />
          {state.error.status === 409 && <p>一覧に戻って最新の内容を取得し、確認してから申請してください。</p>}
          {uncertain && <p role="status">申請結果が不明です。一覧に戻り、対象の「処理結果を確認」で確認してください。</p>}
        </>
      )}
      {state.kind === 'result' && (
        state.operation.operationState === 'SUCCEEDED' ? (
          <p role="status">申請しました。審査待ちです。公開版は変わりません。</p>
        ) : state.operation.operationState === 'FAILED' ? (
          <p role="alert">申請に失敗しました。{state.operation.message}</p>
        ) : (
          <OperationCheck operationId={state.operation.operationId} load={(id) => api.getDraftOperation(id)}
            onDone={(operation) => setState({ kind: 'result', operation })} onRelogin={onRelogin} />
        )
      )}
      <button type="button" disabled={state.kind === 'submitting'} onClick={onBack}>一覧に戻る</button>
    </section>
  )
}
