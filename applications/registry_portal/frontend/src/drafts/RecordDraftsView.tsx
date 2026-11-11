import { useEffect, useState } from 'react'
import { DeprecationRequestForm } from './DeprecationRequestForm'
import { OperationCheck } from './OperationCheck'
import { RecordHistoryToggle } from './RecordHistoryView'
import { SubmissionView } from './SubmissionView'
import { ApiError, toApiError, type ApiClient, type Role } from '../api/client'
import { DATA_CLASSIFICATIONS } from '../api/dataClassification'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { CATALOG_RECORD_TYPES, RECORD_TYPE_LABELS } from '../api/recordType'
import {
  DEFAULT_DRAFT_ENVIRONMENT,
  DEPRECATED_STATUS,
  isEditableStatus,
  isUncertainWriteError,
  settlesIdempotencyKey,
  type RecordDraft,
  type DraftDetail,
  type DraftOperation,
  type DraftPage,
} from './api'
import {
  COMMON_TEXT_FIELDS,
  DESCRIPTOR_FIELD_SPECS,
  DESCRIPTOR_LABELS,
  RECORD_TYPE_DESCRIPTORS,
  type FieldInput,
} from './descriptors'
import { draftPayload, emptyDraft, isSameDraft, parseDraftJson, switchKind } from './draftForm'

type Props = { api: ApiClient; roles: Role[]; teams: string[]; onRelogin: () => void }
type Screen =
  | { kind: 'list' }
  | { kind: 'new'; key: string }
  | { kind: 'edit'; detail: DraftDetail }
  | { kind: 'submit'; detail: DraftDetail }
type PageState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: DraftPage }
  | { kind: 'error'; error: ApiError }

export function RecordDraftsView({ api, roles, teams, onRelogin }: Props) {
  const [screen, setScreen] = useState<Screen>({ kind: 'list' })
  const [page, setPage] = useState<PageState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState<ApiError | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const allowed = roles.includes('Publisher') && teams.length > 0
  useEffect(() => {
    if (!allowed || screen.kind !== 'list') return
    let active = true
    api.listDrafts(token).then(
      (result) => {
        if (active) setPage({ kind: 'ready', page: result })
      },
      (failure: unknown) => {
        if (active)
          setPage({
            kind: 'error',
            error: toApiError(failure, '下書き一覧を取得できませんでした。'),
          })
      },
    )
    return () => {
      active = false
    }
  }, [api, allowed, screen.kind, token, revision])

  if (!roles.includes('Publisher')) return null
  if (!allowed)
    return (
      <section>
        <h2>下書きの管理</h2>
        <p>作成・編集には所属チームが必要です。管理者に連絡してください。</p>
      </section>
    )

  async function open(id: string) {
    setLoadingDetail(true)
    setError(null)
    try {
      setScreen({ kind: 'edit', detail: await api.getDraft(id) })
    } catch (failure) {
      setError(toApiError(failure, '下書きを取得できませんでした。'))
    } finally {
      setLoadingDetail(false)
    }
  }
  function back() {
    setScreen({ kind: 'list' })
    setPage({ kind: 'loading' })
    setRevision((value) => value + 1)
    setError(null)
  }
  return (
    <section className="drafts" aria-labelledby="drafts-heading">
      <h2 id="drafts-heading">下書きの管理</h2>
      <p>
        所属チームの Agent（custom・A2A）・MCP サーバー・Skill を登録します。environment は{' '}
        {DEFAULT_DRAFT_ENVIRONMENT} 固定です。保存しても公開されません。
      </p>
      {screen.kind === 'list' ? (
        <>
          <button
            type="button"
            onClick={() => setScreen({ kind: 'new', key: crypto.randomUUID() })}
          >
            下書きを作成
          </button>
          {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
          {loadingDetail && <p role="status">下書きを取得しています…</p>}
          {page.kind === 'loading' ? (
            <p role="status">一覧を読み込んでいます…</p>
          ) : page.kind === 'error' ? (
            <>
              <RequestErrorAlert error={page.error} onRelogin={onRelogin} />
              <button type="button" onClick={back}>
                一覧を再読み込み
              </button>
            </>
          ) : (
            <>
              {page.page.items.length === 0 && (
                <p>
                  {page.page.nextToken
                    ? 'このページには所属チームの下書きがありません。次のページを確認してください。'
                    : '所属チームの下書き対象はありません。'}
                </p>
              )}
              <ul className="catalog-items">
                {page.page.items.map((item) => (
                  <li key={item.recordId}>
                    <h3>{item.displayName}</h3>
                    <p>
                      {RECORD_TYPE_LABELS[item.recordType]}・{item.name}・{item.ownerTeam}・版{' '}
                      {item.recordVersion}
                    </p>
                    <p>
                      {item.hasDraft ? '下書きあり' : '下書きなし'} /{' '}
                      {item.publishedVersion ? `公開中 ${item.publishedVersion}` : '公開版なし'}
                    </p>
                    <p>
                      AWS の処理状態 {item.status}
                      {item.operationId && ' / 結果確認中'}
                    </p>
                    <button
                      type="button"
                      disabled={
                        loadingDetail || item.operationId !== null || !isEditableStatus(item.status)
                      }
                      onClick={() => void open(item.recordId)}
                    >
                      編集
                    </button>
                    {item.operationId && (
                      <OperationCheck
                        api={api}
                        operationId={item.operationId}
                        onDone={back}
                        onRelogin={onRelogin}
                      />
                    )}
                  </li>
                ))}
              </ul>
              <nav className="pager" aria-label="下書きのページ">
                <button
                  type="button"
                  disabled={token === undefined}
                  onClick={() => {
                    setPage({ kind: 'loading' })
                    setToken(undefined)
                  }}
                >
                  最初のページ
                </button>
                <button
                  type="button"
                  disabled={page.page.nextToken === null}
                  onClick={() => {
                    if (page.page.nextToken) {
                      setToken(page.page.nextToken)
                      setPage({ kind: 'loading' })
                    }
                  }}
                >
                  次のページ
                </button>
              </nav>
            </>
          )}
        </>
      ) : screen.kind === 'submit' ? (
        <SubmissionView api={api} detail={screen.detail} onBack={back} onRelogin={onRelogin} />
      ) : (
        <DraftEditor
          key={screen.kind === 'new' ? screen.key : screen.detail.recordId}
          api={api}
          initial={screen}
          teams={teams}
          onBack={back}
          onSubmitDraft={(detail) => setScreen({ kind: 'submit', detail })}
          onRelogin={onRelogin}
        />
      )}
    </section>
  )
}

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
/** 1つの入力項目。共通項目と descriptor ごとの項目で、入力欄の種類（FieldInput）に応じて描き分ける */
function FieldControl({
  name,
  label,
  input,
  maxLength,
  value,
  options = [],
  optional = false,
  readOnly = false,
  onChange,
}: {
  name: string
  label: string
  input: FieldInput
  maxLength: number
  value: string
  options?: readonly string[]
  optional?: boolean
  readOnly?: boolean
  onChange: (value: string) => void
}) {
  return (
    <label>
      {label}
      {input === 'select' ? (
        <select name={name} value={value} onChange={(event) => onChange(event.target.value)}>
          {options.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      ) : input === 'textarea' ? (
        <textarea
          name={name}
          required={!optional}
          maxLength={maxLength}
          rows={maxLength > 4096 ? 10 : 4}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          name={name}
          required={!optional}
          maxLength={maxLength}
          value={value}
          readOnly={readOnly}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  )
}

/** 種別・descriptor の選択と、共通項目・descriptor ごとの入力項目 */
function KindFields({
  draft,
  isNew,
  onChange,
}: {
  draft: RecordDraft
  isNew: boolean
  onChange: (draft: RecordDraft) => void
}) {
  // 種別は作成後に変えられない。descriptor を選べるのは複数の descriptor を持つ種別（Agent）だけ
  const descriptors = RECORD_TYPE_DESCRIPTORS[draft.recordType]
  function setText(key: string, value: string) {
    onChange({ ...draft, [key]: value })
  }
  return (
    <>
      <label>
        種別
        <select
          name="recordType"
          value={draft.recordType}
          disabled={!isNew}
          onChange={(event) => {
            const type = CATALOG_RECORD_TYPES.find((value) => value === event.target.value)
            if (type) onChange(switchKind(draft, type))
          }}
        >
          {CATALOG_RECORD_TYPES.map((type) => (
            <option key={type} value={type}>
              {RECORD_TYPE_LABELS[type]}
            </option>
          ))}
        </select>
      </label>
      <label>
        登録の形式（descriptor）
        <select
          name="descriptorType"
          value={draft.descriptorType}
          disabled={descriptors.length === 1}
          onChange={(event) => {
            const descriptor = descriptors.find((value) => value === event.target.value)
            if (descriptor) onChange(switchKind(draft, draft.recordType, descriptor))
          }}
        >
          {descriptors.map((descriptor) => (
            <option key={descriptor} value={descriptor}>
              {DESCRIPTOR_LABELS[descriptor]}
            </option>
          ))}
        </select>
      </label>
      {COMMON_TEXT_FIELDS.map((spec) => (
        <FieldControl
          key={spec.key}
          name={spec.key}
          label={spec.label}
          input={spec.input}
          maxLength={spec.maxLength}
          value={draft[spec.key]}
          readOnly={spec.key === 'name' && !isNew}
          onChange={(value) => setText(spec.key, value)}
        />
      ))}
      {DESCRIPTOR_FIELD_SPECS[draft.descriptorType].map((spec) => (
        <FieldControl
          key={spec.key}
          name={spec.key}
          label={spec.label}
          input={spec.input}
          maxLength={spec.maxLength}
          value={draft[spec.key] ?? ''}
          options={spec.options}
          optional={spec.optional}
          onChange={(value) => setText(spec.key, value)}
        />
      ))}
    </>
  )
}

/** 所有チーム・environment（dev 固定）・データ分類（public / private） */
function MetadataFields({
  draft,
  teams,
  isNew,
  onChange,
}: {
  draft: RecordDraft
  teams: string[]
  isNew: boolean
  onChange: (draft: RecordDraft) => void
}) {
  return (
    <>
      <label>
        所有チーム
        <select
          name="ownerTeam"
          value={draft.ownerTeam}
          disabled={!isNew}
          onChange={(event) => onChange({ ...draft, ownerTeam: event.target.value })}
        >
          {teams.map((team) => (
            <option key={team}>{team}</option>
          ))}
        </select>
      </label>
      <label>
        environment
        <input name="environment" value={draft.environment} readOnly />
      </label>
      <label>
        データ分類
        <select
          name="dataClassification"
          value={draft.dataClassification}
          onChange={(event) => {
            const classification = DATA_CLASSIFICATIONS.find(
              (value) => value === event.target.value,
            )
            if (classification) onChange({ ...draft, dataClassification: classification })
          }}
        >
          {DATA_CLASSIFICATIONS.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
    </>
  )
}

function DraftEditor({
  api,
  initial,
  teams,
  onBack,
  onSubmitDraft,
  onRelogin,
}: {
  api: ApiClient
  initial: Extract<Screen, { kind: 'new' | 'edit' }>
  teams: string[]
  onBack: () => void
  onSubmitDraft: (detail: DraftDetail) => void
  onRelogin: () => void
}) {
  const [draft, setDraft] = useState<RecordDraft>(() =>
    initial.kind === 'edit' ? initial.detail.draft : emptyDraft(teams[0] ?? ''),
  )
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
    if (operation.operationState === 'SUCCEEDED') setSavedDetail(operation)
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
  return (
    <>
      <h3>{text.title}</h3>
      <p>
        秘密情報を入力せず、接続先や認証情報の参照を記載してください。申請するときは、公開中の版より大きい版が必要です。
      </p>
      {initial.kind === 'edit' && initial.detail.status === 'PENDING_APPROVAL' && (
        <p>審査待ちです。下書きを編集して保存すると、元の申請は無効になります。</p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <fieldset disabled={disabled}>
          <button
            type="button"
            onClick={() => (inputMode === 'form' ? startJson() : setInputMode('form'))}
          >
            {inputMode === 'form' ? 'JSON で入力' : 'フォームで入力'}
          </button>
          {inputMode === 'json' ? (
            <label>
              下書きの JSON
              <textarea
                name="draftJson"
                rows={16}
                value={jsonText}
                onChange={(event) => changeJson(event.target.value)}
              />
            </label>
          ) : (
            <KindFields draft={draft} isNew={isNew} onChange={setDraft} />
          )}
          {inputMode === 'form' && (
            <MetadataFields draft={draft} teams={teams} isNew={isNew} onChange={setDraft} />
          )}
        </fieldset>
        {inputMode === 'json' && jsonError && <p role="alert">{jsonError}</p>}
        <button type="submit" disabled={saveDisabled}>
          下書きを保存
        </button>
      </form>
      {save.kind === 'saving' && <p role="status">保存しています…</p>}
      {canRetryCreate && (
        <button type="button" onClick={() => void submit(true)}>
          同じ入力とキーで作成を再試行
        </button>
      )}
      {save.kind === 'error' && (
        <>
          <RequestErrorAlert error={save.error} onRelogin={onRelogin} />
          {save.error.status === 409 && !isNew && (
            <>
              <p>
                他の人が変更した可能性があります。再読み込みすると入力中の内容を最新の内容に置き換えます。
              </p>
              <button type="button" onClick={() => void reload()}>
                最新の内容を再読み込み
              </button>
            </>
          )}
          {uncertain && <p>{text.uncertain}</p>}
        </>
      )}
      {save.kind === 'result' &&
        (save.operation.operationState === 'SUCCEEDED' ? (
          <p role="status">
            下書きを保存しました。公開版は変わりません。
            {initial.kind === 'edit' && initial.detail.status === 'PENDING_APPROVAL' &&
              '元の申請を無効にしました。再度申請してください。'}
          </p>
        ) : save.operation.operationState === 'FAILED' ? (
          <p role="alert">{save.operation.message}</p>
        ) : (
          <OperationCheck
            api={api}
            operationId={save.operation.operationId}
            onDone={(operation) => {
              if (operation.operationState === 'SUCCEEDED') onBack()
              else receiveResult(operation)
            }}
            onRelogin={onRelogin}
          />
        ))}
      {savedDetail && (
        <>
          <p>申請には保存済みの内容を使います。変更中の場合は先に保存してください。</p>
          <button
            type="button"
            disabled={
              savedDetail.status !== 'DRAFT' ||
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
        </>
      )}
      {initial.kind === 'edit' && initial.detail.status !== DEPRECATED_STATUS && (
        <DeprecationRequestForm
          api={api}
          recordId={initial.detail.recordId}
          onRelogin={onRelogin}
        />
      )}
      {initial.kind === 'edit' && (
        <RecordHistoryToggle api={api} recordId={initial.detail.recordId} onRelogin={onRelogin} />
      )}
      <button
        type="button"
        onClick={onBack}
        disabled={
          save.kind === 'saving' || (isNew && disabled && save.kind === 'error')
        }
      >
        一覧に戻る
      </button>
    </>
  )
}
