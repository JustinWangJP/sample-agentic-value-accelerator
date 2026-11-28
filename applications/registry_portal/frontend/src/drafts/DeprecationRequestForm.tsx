// Publisher の廃止依頼（#13）。Curator が審査待ちの一覧で、廃止するか見送るかを判断する。
// 廃止依頼は AWS の状態を変えないため、Idempotency-Key は使わない（未解決の依頼の重複と、
// 別の操作の処理中は 409 になる。どちらかはサーバーのメッセージで伝える）
import { useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { LoadingState } from '../components/StatePanel'
import { callout, fieldLabel } from '../ui/classes'
import { DEPRECATION_REASON_MAX_LENGTH, type DeprecationRequest } from './api'

type State =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'sent'; request: DeprecationRequest }

/** 廃止依頼のフォーム。編集画面の対象レコードについて、所有チームの Publisher が依頼する */
export function DeprecationRequestForm({
  api,
  recordId,
  onRelogin,
}: {
  api: ApiClient
  recordId: string
  onRelogin: () => void
}) {
  const [reason, setReason] = useState('')
  const [state, setState] = useState<State>({ kind: 'idle' })

  async function request() {
    const trimmed = reason.trim()
    if (!trimmed) return
    setState({ kind: 'sending' })
    try {
      setState({ kind: 'sent', request: await api.requestDeprecation(recordId, { reason: trimmed }) })
    } catch (failure) {
      setState({ kind: 'error', error: toApiError(failure, '廃止依頼を送信できませんでした。') })
    }
  }

  return (
    <section aria-labelledby="deprecation-request-heading" className="rounded-xl border border-rose-200 bg-rose-50/40 p-5 space-y-3">
      <h3 id="deprecation-request-heading" className="text-sm font-semibold text-rose-950">廃止を依頼する</h3>
      <p className="text-xs text-slate-600 leading-relaxed">
        使われなくなったレコードの廃止を Curator に依頼できます。依頼は審査待ちの一覧に表示され、
        Curator が廃止するか、理由を付けて見送るかを判断します（先に公開停止で止めることがあります）。
        結果は操作履歴で確認できます。
      </p>
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault()
          void request()
        }}
      >
        <label className={fieldLabel}>
          廃止依頼の理由（必須。終了の背景を記録します）
          <textarea
            name="deprecationReason"
            required
            maxLength={DEPRECATION_REASON_MAX_LENGTH}
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center justify-center px-4 py-2 rounded-lg border border-rose-300 bg-white hover:bg-rose-100 text-rose-800 text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={state.kind === 'sending' || !reason.trim()}
        >
          廃止を依頼する
        </button>
      </form>
      {state.kind === 'sending' && <LoadingState>依頼を送信しています…</LoadingState>}
      {state.kind === 'error' && <RequestErrorAlert error={state.error} onRelogin={onRelogin} />}
      {state.kind === 'sent' && (
        <p role="status" className={callout.success}>
          廃止依頼を送信しました。Curator が廃止するか見送るかを判断します（依頼 ID{' '}
          {state.request.requestId}）。結果は操作履歴で確認できます。
        </p>
      )}
    </section>
  )
}
