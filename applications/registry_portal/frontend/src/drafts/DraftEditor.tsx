import { useState } from 'react'
import { ApiError, toApiError, type ApiClient } from '../api/client'
import { DetailBreadcrumb } from '../components/NavigationControls'
import { OperationCheck } from '../components/OperationCheck'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { LoadingState } from '../components/StatePanel'
import { useUnsavedChangesGuard } from '../routing/navigation'
import type { Route } from '../routing/routes'
import { badge, button, callout, card, fieldLabel, note, pageTitle } from '../ui/classes'
import {
  DEPRECATED_STATUS,
  PENDING_APPROVAL_STATUS,
  isUncertainWriteError,
  recordStatusLabel,
  settlesIdempotencyKey,
  type DraftDetail,
  type DraftOperation,
  type RecordDraft,
  type RecordStatus,
} from './api'
import { DeprecationRequestForm } from './DeprecationRequestForm'
import { DescriptorFields, FORM_SECTION, KindFields, MetadataFields } from './DraftFormSections'
import { draftPayload, emptyDraft, isSameDraft, parseDraftJson } from './draftForm'
import { RecordHistoryToggle } from './RecordHistoryView'

export type BackOptions = { force?: boolean }
export type EditorInitial = { kind: 'new'; key: string } | { kind: 'edit'; detail: DraftDetail }

/**
 * 下書き。申請できるのはこの状態だけ。バックエンドの DRAFT_STATUS と揃える。
 * 各 api.ts は変更しない方針（Issue #85）のため、使う画面のここで定義する
 */
const DRAFT_STATUS: RecordStatus = 'DRAFT'

const DRAFTS_LIST_ROUTE: Route = { screen: 'drafts', view: { kind: 'list' } }

/** 作成と編集で異なる文言 */
const EDITOR_TEXT = {
  new: {
    title: '下書きを作成',
    uncertain:
      '作成結果が不明です。同じ入力とキーで保存を再試行してください。この画面を閉じて新しく作成すると重複する可能性があります。',
  },
  edit: {
    title: '下書きを編集',
    uncertain:
      '更新結果が不明です。一覧に戻り、対象の「処理結果を確認」で結果を確認してください。',
  },
} as const

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'result'; operation: DraftOperation }

/** 作成画面。開くたびに新しい Idempotency-Key で始める */
export function NewDraftScreen(props: {
  api: ApiClient
  teams: string[]
  onBack: (options?: BackOptions) => void
  onSubmitDraft: (detail: DraftDetail) => void
  onRelogin: () => void
}) {
  const [key] = useState(() => crypto.randomUUID())
  return <DraftEditor {...props} initial={{ kind: 'new', key }} />
}

export function DraftEditor({
  api,
  initial,
  teams,
  onBack,
  onSubmitDraft,
  onRelogin,
}: {
  api: ApiClient
  initial: EditorInitial
  teams: string[]
  onBack: (options?: BackOptions) => void
  onSubmitDraft: (detail: DraftDetail) => void
  onRelogin: () => void
}) {
  const [draft, setDraft] = useState<RecordDraft>(() =>
    initial.kind === 'edit' ? initial.detail.draft : emptyDraft(teams[0] ?? ''),
  )
  // 新規作成で、未保存の変更かどうかを比べる初期値
  const [initialDraft] = useState(draft)
  const [savedDetail, setSavedDetail] = useState<DraftDetail | null>(
    initial.kind === 'edit' ? initial.detail : null,
  )
  const [inputMode, setInputMode] = useState<'form' | 'json'>('form')
  const [jsonText, setJsonText] = useState('')
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [hash, setHash] = useState(initial.kind === 'edit' ? initial.detail.contentHash : '')
  const [createKey, setCreateKey] = useState(initial.kind === 'new' ? initial.key : '')
  const [save, setSave] = useState<SaveState>({ kind: 'idle' })
  const isNew = initial.kind === 'new'
  const text = EDITOR_TEXT[initial.kind]
  const resultLocked = save.kind === 'result' && save.operation.operationState !== 'FAILED'
  const uncertain = save.kind === 'error' && isUncertainWriteError(save.error)
  const saveDisabled =
    save.kind === 'saving' ||
    resultLocked ||
    (!isNew && uncertain) ||
    (inputMode === 'json' && jsonError !== null)
  const disabled = saveDisabled || uncertain
  const canRetryCreate =
    isNew && save.kind === 'result' && save.operation.operationState === 'VERIFYING'
  // 未保存の変更: 最後に保存した内容（新規は初期値）との差分と、JSON として読み取れない入力
  const dirty =
    !isSameDraft(draft, savedDetail?.draft ?? initialDraft) || (inputMode === 'json' && jsonError !== null)
  useUnsavedChangesGuard(dirty)

  async function submit(retryCreate = false) {
    if (saveDisabled && !(retryCreate && canRetryCreate)) return
    setSave({ kind: 'saving' })
    try {
      const operation =
        initial.kind === 'new'
          ? await api.createDraft(draftPayload(draft), createKey)
          : await api.updateDraft(initial.detail.recordId, draftPayload(draft), hash)
      receiveResult(operation)
    } catch (failure) {
      const error = toApiError(failure, '下書きを保存できませんでした。')
      if (isNew && settlesIdempotencyKey(error)) setCreateKey(crypto.randomUUID())
      setSave({ kind: 'error', error })
    }
  }

  function receiveResult(operation: DraftOperation) {
    if (operation.operationState === 'SUCCEEDED') {
      // サーバーが正規化した下書き・ハッシュに揃えて、保存成功後は未保存状態を解除する
      setSavedDetail(operation)
      setDraft(operation.draft)
      setHash(operation.contentHash)
      if (inputMode === 'json') {
        setJsonText(JSON.stringify(draftPayload(operation.draft), null, 2))
        setJsonError(null)
      }
    }
    if (isNew && operation.operationState === 'FAILED') {
      setCreateKey(crypto.randomUUID())
    }
    setSave({ kind: 'result', operation })
  }

  function startJson() {
    setJsonText(JSON.stringify(draftPayload(draft), null, 2))
    setJsonError(null)
    setInputMode('json')
  }

  function changeJson(text: string) {
    setJsonText(text)
    const result = parseDraftJson(text, draft)
    if ('draft' in result) setDraft(result.draft)
    setJsonError('error' in result ? result.error : null)
  }

  async function reload() {
    if (initial.kind !== 'edit') return
    setSave({ kind: 'saving' })
    try {
      const latest = await api.getDraft(initial.detail.recordId)
      setSavedDetail(latest)
      setDraft(latest.draft)
      setJsonError(null)
      setHash(latest.contentHash)
      setSave({ kind: 'idle' })
    } catch (failure) {
      setSave({ kind: 'error', error: toApiError(failure, '最新の内容を取得できませんでした。') })
    }
  }

  const backDisabled = save.kind === 'saving' || (isNew && disabled && save.kind === 'error')
  return (
    <section aria-labelledby="draft-editor-heading" className="space-y-6">
      <DetailBreadcrumb
        parentTo={DRAFTS_LIST_ROUTE}
        parentLabel="下書き・申請"
        current={text.title}
        onBack={() => onBack()}
        backDisabled={backDisabled}
      >
        {initial.kind === 'edit' && (
          <div className="w-56">
            <RecordHistoryToggle api={api} recordId={initial.detail.recordId} onRelogin={onRelogin} />
          </div>
        )}
      </DetailBreadcrumb>
      <div>
        <div className="flex flex-wrap items-center gap-2 mb-1">
          {initial.kind === 'edit' && <span className={badge.neutral}>{recordStatusLabel(initial.detail.status)}</span>}
          {savedDetail?.publishedVersion && <span className={badge.success}>公開中の版: {savedDetail.publishedVersion}</span>}
          {dirty && <span className={badge.warning}>未保存の変更あり</span>}
        </div>
        <h1 id="draft-editor-heading" className={pageTitle}>
          {text.title}
        </h1>
        {initial.kind === 'edit' && (
          <p className="mt-1 text-xs font-mono text-slate-500 break-all">recordId: {initial.detail.recordId}</p>
        )}
      </div>
      {savedDetail?.initializationNotice && (
        <p role="status" className={callout.info}>
          {savedDetail.initializationNotice}
        </p>
      )}
      <p className={callout.info}>
        秘密情報を入力せず、接続先や認証情報の参照を記載してください。申請するときは、公開中の版より大きい版が必要です。
      </p>
      {initial.kind === 'edit' && initial.detail.status === PENDING_APPROVAL_STATUS && (
        <p className={callout.warning}>審査待ちです。下書きを編集して保存すると、元の申請は無効になります。</p>
      )}
      <form
        className="space-y-6 max-w-4xl"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <fieldset disabled={disabled} className="space-y-6">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs text-slate-500">
              入力方法: <strong className="text-slate-800">{inputMode === 'form' ? 'フォーム' : 'JSON'}</strong>
            </span>
            <button
              type="button"
              className={button.small}
              onClick={() => (inputMode === 'form' ? startJson() : setInputMode('form'))}
            >
              {inputMode === 'form' ? 'JSON で入力' : 'フォームで入力'}
            </button>
          </div>
          {inputMode === 'json' ? (
            <section className={FORM_SECTION}>
              <label className={fieldLabel}>
                下書きの JSON
                <textarea
                  name="draftJson"
                  rows={16}
                  value={jsonText}
                  onChange={(event) => changeJson(event.target.value)}
                />
              </label>
            </section>
          ) : (
            <>
              <KindFields draft={draft} isNew={isNew} onChange={setDraft} />
              <MetadataFields draft={draft} teams={teams} isNew={isNew} onChange={setDraft} />
              <DescriptorFields draft={draft} onChange={setDraft} />
            </>
          )}
        </fieldset>
        {inputMode === 'json' && jsonError && (
          <p role="alert" className={callout.danger}>
            {jsonError}
          </p>
        )}
        <div className="sticky bottom-0 z-20 -mx-4 sm:mx-0 px-4 py-3 bg-white/95 backdrop-blur border-t sm:border sm:rounded-xl border-slate-200 shadow-lg flex flex-wrap items-center justify-between gap-3">
          <span className="text-xs text-slate-500">
            {dirty ? '保存していない変更があります。' : '保存済みの内容と同じです。'}
          </span>
          <button type="submit" className={button.primary} disabled={saveDisabled}>
            下書きを保存
          </button>
        </div>
      </form>
      {save.kind === 'saving' && <LoadingState>保存しています…</LoadingState>}
      {canRetryCreate && (
        <button type="button" className={button.secondary} onClick={() => void submit(true)}>
          同じ入力とキーで作成を再試行
        </button>
      )}
      {save.kind === 'error' && (
        <RequestErrorAlert error={save.error} onRelogin={onRelogin}>
          {save.error.status === 409 && !isNew && (
            <>
              <p className="text-xs text-rose-800">
                他の人が変更した可能性があります。再読み込みすると入力中の内容を最新の内容に置き換えます。
              </p>
              <button type="button" className={button.dangerOutline} onClick={() => void reload()}>
                最新の内容を再読み込み
              </button>
            </>
          )}
          {uncertain && <p className="text-xs text-rose-800">{text.uncertain}</p>}
        </RequestErrorAlert>
      )}
      {save.kind === 'result' &&
        (save.operation.operationState === 'SUCCEEDED' ? (
          <p role="status" className={callout.success}>
            下書きを保存しました。公開版は変わりません。
            {initial.kind === 'edit' && initial.detail.status === PENDING_APPROVAL_STATUS &&
              '元の申請を無効にしました。再度申請してください。'}
          </p>
        ) : save.operation.operationState === 'FAILED' ? (
          <p role="alert" className={callout.danger}>
            {save.operation.message}
          </p>
        ) : (
          <OperationCheck
            operationId={save.operation.operationId}
            load={(id) => api.getDraftOperation(id)}
            onDone={(operation) => {
              // 保存が確定したら一覧へ戻る。保存済みの内容なので未保存の確認は出さない
              if (operation.operationState === 'SUCCEEDED') onBack({ force: true })
              else receiveResult(operation)
            }}
            onRelogin={onRelogin}
          />
        ))}
      {savedDetail && (
        <section className={`${card} p-5 space-y-2`}>
          <h2 className="text-sm font-semibold text-slate-900">申請</h2>
          <p className={note}>申請には保存済みの内容を使います。変更中の場合は先に保存してください。</p>
          <button
            type="button"
            className={button.primary}
            disabled={
              savedDetail.status !== DRAFT_STATUS ||
              !savedDetail.hasDraft ||
              save.kind === 'saving' ||
              (save.kind === 'result' && save.operation.operationState === 'VERIFYING') ||
              uncertain ||
              jsonError !== null ||
              !isSameDraft(draft, savedDetail.draft)
            }
            onClick={() => onSubmitDraft(savedDetail)}
          >
            保存済みの内容を申請
          </button>
        </section>
      )}
      {initial.kind === 'edit' && initial.detail.status !== DEPRECATED_STATUS && (
        <DeprecationRequestForm
          api={api}
          recordId={initial.detail.recordId}
          onRelogin={onRelogin}
        />
      )}
    </section>
  )
}
