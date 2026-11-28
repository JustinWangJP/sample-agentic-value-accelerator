import { useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { LoadingState } from '../components/StatePanel'
import { badge, button, callout, card, codeBlock, fieldLabel, note, pageTitle } from '../ui/classes'
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
    <section aria-labelledby="submission-heading" className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="パンくずリスト" className="flex items-center gap-2 text-xs text-slate-500">
          <span>下書き・申請</span>
          <span aria-hidden="true">/</span>
          <span className="text-slate-900 font-medium">申請</span>
        </nav>
        <button type="button" className={button.small} disabled={state.kind === 'submitting'} onClick={onBack}>
          一覧に戻る
        </button>
      </div>
      <section className={`${card} p-6 space-y-2`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className={badge.neutral}>{RECORD_TYPE_LABELS[detail.draft.recordType]}</span>
          <span className={detail.publishedVersion ? badge.success : badge.neutral}>
            {detail.publishedVersion ? `公開中の版 ${detail.publishedVersion}` : '公開版なし'}
          </span>
        </div>
        <h1 id="submission-heading" className={pageTitle}>
          下書きを申請
        </h1>
        <p className="text-sm text-slate-700 break-all">
          {detail.draft.displayName}（{RECORD_TYPE_LABELS[detail.draft.recordType]}）
        </p>
        <p className="text-xs font-mono text-slate-500">
          版 {detail.draft.recordVersion} / 所有チーム {detail.draft.ownerTeam}
        </p>
        <p className="text-xs text-slate-600">
          {detail.publishedVersion ? `公開中の版 ${detail.publishedVersion}` : '公開版なし'}。
          保存済みの内容を審査に提出します。申請しても公開されません。
        </p>
        <details className="pt-2">
          <summary className="text-xs font-semibold text-slate-700 select-none">申請する保存済みの内容を確認</summary>
          <pre className={`mt-3 ${codeBlock}`}>{JSON.stringify(detail.draft, null, 2)}</pre>
        </details>
      </section>
      <form
        className={`${card} p-6`}
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <fieldset disabled={locked} className="space-y-4">
          <label className={fieldLabel}>
            申請理由
            <textarea
              name="reason"
              required
              maxLength={SUBMISSION_REASON_MAX_LENGTH}
              rows={4}
              className="font-sans text-sm"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {isAgent && (
            <fieldset className="rounded-lg border border-slate-200 p-4 space-y-3">
              <legend className="px-1 text-xs font-semibold text-slate-700">
                関連参照（任意、最大{RELATED_REFERENCES_MAX}件）
              </legend>
              <p className={note}>公開済みのカタログ項目のレコード ID と版を指定してください。</p>
              {references.map((reference, index) => (
                <div key={index} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-2 items-end">
                  <label className={fieldLabel}>
                    レコード ID {index + 1}
                    <input
                      name={`referenceId-${index}`}
                      className="font-mono"
                      required
                      pattern={RECORD_ID_PATTERN}
                      maxLength={RECORD_ID_LENGTH}
                      value={reference.recordId}
                      onChange={(event) =>
                        setReferences(
                          references.map((item, i) =>
                            i === index ? { ...item, recordId: event.target.value } : item,
                          ),
                        )
                      }
                    />
                  </label>
                  <label className={fieldLabel}>
                    レコードの版 {index + 1}
                    <input
                      name={`referenceVersion-${index}`}
                      className="font-mono"
                      required
                      value={reference.recordVersion}
                      onChange={(event) =>
                        setReferences(
                          references.map((item, i) =>
                            i === index ? { ...item, recordVersion: event.target.value } : item,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    type="button"
                    className={button.dangerOutline}
                    onClick={() => setReferences(references.filter((_, i) => i !== index))}
                  >
                    関連参照 {index + 1} を削除
                  </button>
                </div>
              ))}
              <button
                type="button"
                className={button.small}
                disabled={references.length >= RELATED_REFERENCES_MAX}
                onClick={() => setReferences([...references, { recordId: '', recordVersion: '' }])}
              >
                関連参照を追加
              </button>
            </fieldset>
          )}
          <button type="submit" className={button.primary} disabled={locked || !reason.trim()}>
            申請する
          </button>
        </fieldset>
      </form>
      {state.kind === 'submitting' && <LoadingState>申請しています…</LoadingState>}
      {state.kind === 'error' && (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin}>
          {state.error.status === 409 && (
            <p className="text-xs text-rose-800">一覧に戻って最新の内容を取得し、確認してから申請してください。</p>
          )}
          {uncertain && (
            <p role="status" className="text-xs text-rose-800">
              申請結果が不明です。一覧に戻り、対象の「処理結果を確認」で確認してください。
            </p>
          )}
        </RequestErrorAlert>
      )}
      {state.kind === 'result' &&
        (state.operation.operationState === 'SUCCEEDED' ? (
          <p role="status" className={callout.success}>
            申請しました。審査待ちです。公開版は変わりません。
          </p>
        ) : state.operation.operationState === 'FAILED' ? (
          <p role="alert" className={callout.danger}>
            申請に失敗しました。{state.operation.message}
          </p>
        ) : (
          <OperationCheck
            operationId={state.operation.operationId}
            load={(id) => api.getDraftOperation(id)}
            onDone={(operation) => setState({ kind: 'result', operation })}
            onRelogin={onRelogin}
          />
        ))}
    </section>
  )
}
