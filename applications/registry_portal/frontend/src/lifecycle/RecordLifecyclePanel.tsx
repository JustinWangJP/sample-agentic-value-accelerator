import { useEffect, useState } from 'react'
import { toApiError, type ApiClient, type ApiError } from '../api/client'
import { RECORD_TYPE_LABELS } from '../api/recordType'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { RecordHistoryToggle } from '../drafts/RecordHistoryView'
import {
  DEPRECATED_STATUS,
  DEPRECATION_REASON_MAX_LENGTH,
  PENDING_APPROVAL_STATUS,
  isUncertainWriteError,
  settlesIdempotencyKey,
  type DeprecationRequest,
  type RecordStatus,
} from '../drafts/api'
import type { DecisionOperation } from '../reviews/api'
import { DecisionOperationCheck } from '../reviews/DecisionOperationCheck'
import {
  LIFECYCLE_ACTIONS,
  LIFECYCLE_REASON_MAX_LENGTH,
  elapsedDays,
  type LifecycleAction,
  type LifecycleView,
} from './api'

type Props = {
  api: ApiClient
  recordId: string
  onRelogin: () => void
  /** 公開停止・廃止・見送りが確定したとき（一覧の再読み込みなどに使う） */
  onChanged?: () => void
}

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; view: LifecycleView }
  | { kind: 'error'; error: ApiError }

type ActionState =
  | { kind: 'idle' }
  | { kind: 'busy' }
  /** retry は、同じ Idempotency-Key で送り直せる操作。結果が不明なときだけ値を持つ */
  | { kind: 'error'; error: ApiError; retry: LifecycleAction | null }
  | { kind: 'result'; operation: DecisionOperation }
  | { kind: 'declined'; request: DeprecationRequest }

/** 操作ごとの画面の文言。公開停止は却下で発見できなくし、廃止は元に戻せない */
const ACTION_COPY: Record<
  LifecycleAction,
  { legend: string; description: string; reasonLabel: string; submit: string; failure: string }
> = {
  suspend: {
    legend: '公開停止',
    description:
      '問題が見つかったものを、理由を付けて却下し、発見できなくします。廃止依頼は閉じないため、' +
      '影響を確かめてから廃止するか見送るかを判断できます。再公開には Publisher の編集と再申請が必要です。',
    reasonLabel: '公開停止の理由（必須。Publisher と利用者への説明として記録します）',
    submit: '公開停止する',
    failure: '公開停止できませんでした。',
  },
  deprecate: {
    legend: '廃止',
    description:
      '廃止は元に戻せません。レコードは DEPRECATED になり、編集も再公開もできなくなります。' +
      '未解決の廃止依頼は、この廃止で解決済みになります。',
    reasonLabel: '廃止理由（必須。判断の根拠として記録します）',
    submit: '廃止する',
    failure: '廃止できませんでした。',
  },
}

function statusLabel(status: RecordStatus): string {
  return status === PENDING_APPROVAL_STATUS ? '審査待ち' : status
}

function newKeys(): Record<LifecycleAction, string> {
  return { suspend: crypto.randomUUID(), deprecate: crypto.randomUUID() }
}

/**
 * Curator の公開管理。公開停止・廃止・廃止依頼の見送りを、現在の状態と実行できない理由を見ながら行う。
 * 廃止は理由の入力に加えて確認のチェック（二重確認）を必須にする（設計書「業務上の状態」）
 */
export function RecordLifecyclePanel({ api, recordId, onRelogin, onChanged }: Props) {
  const [state, setState] = useState<ViewState>({ kind: 'loading' })
  const [reasons, setReasons] = useState<Record<LifecycleAction | 'decline', string>>({
    suspend: '',
    deprecate: '',
    decline: '',
  })
  const [confirmed, setConfirmed] = useState(false)
  const [keys, setKeys] = useState(newKeys)
  const [action, setAction] = useState<ActionState>({ kind: 'idle' })
  const loading = state.kind === 'loading'
  const busy = action.kind === 'busy'

  useEffect(() => {
    if (!loading) return
    let active = true
    api.getLifecycle(recordId).then(
      (view) => {
        if (active) setState({ kind: 'ready', view })
      },
      (failure: unknown) => {
        if (active)
          setState({ kind: 'error', error: toApiError(failure, '公開管理の状態を取得できませんでした。') })
      },
    )
    return () => {
      active = false
    }
  }, [api, loading, recordId])

  function reload() {
    setState({ kind: 'loading' })
  }

  function setReason(target: LifecycleAction | 'decline', value: string) {
    setReasons((current) => ({ ...current, [target]: value }))
  }

  function settled() {
    setKeys(newKeys())
    setReasons({ suspend: '', deprecate: '', decline: '' })
    setConfirmed(false)
    onChanged?.()
  }

  async function run(kind: LifecycleAction, retry = false) {
    const reason = reasons[kind].trim()
    if (!reason || (busy && !retry)) return
    if (kind === 'deprecate' && !confirmed) return
    setAction({ kind: 'busy' })
    try {
      const operation =
        kind === 'suspend'
          ? await api.suspendRecord(recordId, { reason }, keys.suspend)
          : await api.deprecateRecord(recordId, { reason, confirm: true }, keys.deprecate)
      setAction({ kind: 'result', operation })
      if (operation.operationState !== 'VERIFYING') settled()
    } catch (failure) {
      const error = toApiError(failure, ACTION_COPY[kind].failure)
      if (settlesIdempotencyKey(error)) setKeys((current) => ({ ...current, [kind]: crypto.randomUUID() }))
      setAction({ kind: 'error', error, retry: isUncertainWriteError(error) ? kind : null })
    }
  }

  async function decline(request: DeprecationRequest) {
    const reason = reasons.decline.trim()
    if (!reason || busy) return
    setAction({ kind: 'busy' })
    try {
      const declined = await api.declineDeprecationRequest(recordId, {
        requestId: request.requestId,
        reason,
      })
      setAction({ kind: 'declined', request: declined })
      settled()
    } catch (failure) {
      setAction({
        kind: 'error',
        error: toApiError(failure, '廃止依頼を見送れませんでした。'),
        retry: null,
      })
    }
  }

  if (state.kind === 'loading') return <p role="status">公開管理の状態を読み込んでいます…</p>
  if (state.kind === 'error')
    return <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={reload} />

  const { view } = state
  const request = view.deprecationRequest
  const finished = action.kind === 'result' || action.kind === 'declined'
  const days = request ? elapsedDays(request.requestedAt) : null

  return (
    <section aria-label={`${view.displayName} の公開管理`} className="lifecycle">
      <h4>公開管理: {view.displayName}</h4>
      <p>
        {RECORD_TYPE_LABELS[view.recordType]}・{view.name}・{view.ownerTeam ?? '所有チーム不明'}・最新の版{' '}
        {view.recordVersion}
      </p>
      <p>
        状態 {statusLabel(view.status)} /{' '}
        {view.publishedVersion ? `公開中の版 ${view.publishedVersion}` : '公開中の版なし'}
      </p>
      {view.suspended && view.status !== DEPRECATED_STATUS && (
        <p>公開停止中です。再公開には Publisher の編集と再申請が必要です。</p>
      )}
      {view.operationId && <p role="status">別の操作が結果確認中です。確定するまで操作できません。</p>}
      {!view.managed && <p role="alert">ポータルで管理されていないため、ここでは操作できません。</p>}
      {request && (
        <section aria-label="未解決の廃止依頼">
          <h5>未解決の廃止依頼</h5>
          <p>
            理由: {request.reason}（依頼者 {request.requestedBy.subject}
            {days !== null && `・${days} 日経過`}）
          </p>
          <p>
            {view.suspended
              ? '公開停止済みです。影響を確かめてから、廃止するか見送るかを判断してください。'
              : '廃止するか見送るかを判断してください。まず公開停止で止めてから判断することもできます。'}
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void decline(request)
            }}
          >
            <fieldset disabled={busy || finished || view.operationId !== null}>
              <legend>依頼を見送る</legend>
              <p>レコードの状態は変えずに、依頼だけを閉じます（公開停止中ならそのまま）。</p>
              <label>
                見送りの理由（必須。Publisher が履歴で確認します）
                <textarea
                  name="declineReason"
                  required
                  maxLength={DEPRECATION_REASON_MAX_LENGTH}
                  rows={3}
                  value={reasons.decline}
                  onChange={(event) => setReason('decline', event.target.value)}
                />
              </label>
              <button type="submit" disabled={busy || !reasons.decline.trim()}>
                依頼を見送る
              </button>
            </fieldset>
          </form>
        </section>
      )}
      {LIFECYCLE_ACTIONS.map((kind) => {
        const availability = view.actions[kind]
        const copy = ACTION_COPY[kind]
        const blocked = !availability.allowed || busy || finished
        return (
          <form
            key={kind}
            onSubmit={(event) => {
              event.preventDefault()
              void run(kind)
            }}
          >
            <fieldset disabled={blocked}>
              <legend>{copy.legend}</legend>
              <p>{copy.description}</p>
              {!availability.allowed && availability.message && (
                <p className="note">{availability.message}</p>
              )}
              <label>
                {copy.reasonLabel}
                <textarea
                  name={`${kind}Reason`}
                  required
                  maxLength={LIFECYCLE_REASON_MAX_LENGTH}
                  rows={3}
                  value={reasons[kind]}
                  onChange={(event) => setReason(kind, event.target.value)}
                />
              </label>
              {kind === 'deprecate' && (
                <label>
                  <input
                    type="checkbox"
                    name="deprecateConfirm"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  廃止は元に戻せないことを確認しました
                </label>
              )}
              <button
                type="submit"
                disabled={blocked || !reasons[kind].trim() || (kind === 'deprecate' && !confirmed)}
              >
                {copy.submit}
              </button>
            </fieldset>
          </form>
        )
      })}
      {busy && <p role="status">実行しています…</p>}
      {action.kind === 'error' && (
        <RequestErrorAlert error={action.error} onRelogin={onRelogin}>
          {action.error.status === 409 && (
            <p>
              状態が変わったか、別の操作が処理中です。最新の状態を読み込んでから判断してください。
            </p>
          )}
          {action.retry !== null && (
            <p>
              結果が不明です。同じ内容で再試行すると、同じ操作として確定を試みます。
              <button
                type="button"
                onClick={() => {
                  if (action.retry !== null) void run(action.retry, true)
                }}
              >
                同じ内容で再試行
              </button>
            </p>
          )}
        </RequestErrorAlert>
      )}
      {action.kind === 'result' && action.operation.operationState === 'SUCCEEDED' && (
        <p role="status">
          {action.operation.status === DEPRECATED_STATUS
            ? '廃止しました。この操作は元に戻せません。'
            : '公開停止しました。廃止依頼があれば、廃止するか見送るかを判断してください。'}
        </p>
      )}
      {action.kind === 'result' && action.operation.operationState === 'VERIFYING' && (
        <DecisionOperationCheck
          api={api}
          operationId={action.operation.operationId}
          onDone={(operation) => {
            setAction({ kind: 'result', operation })
            settled()
          }}
          onRelogin={onRelogin}
        />
      )}
      {action.kind === 'result' && action.operation.operationState === 'FAILED' && (
        <p role="alert">{action.operation.message}</p>
      )}
      {action.kind === 'declined' && (
        <p role="status">廃止依頼を見送りました。理由は履歴に残り、Publisher が確認できます。</p>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setAction({ kind: 'idle' })
          reload()
        }}
      >
        最新の状態を読み込む
      </button>
      <RecordHistoryToggle api={api} recordId={recordId} onRelogin={onRelogin} />
    </section>
  )
}

/** 公開管理を開閉するボタン。カタログの詳細と審査待ちの一覧から、同じ公開管理を開く */
export function RecordLifecycleToggle({
  label = '公開管理を開く',
  ...props
}: Props & { label?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {open ? '公開管理を閉じる' : label}
      </button>
      {open && <RecordLifecyclePanel {...props} />}
    </div>
  )
}
