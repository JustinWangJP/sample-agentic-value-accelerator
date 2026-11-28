import { useEffect, useState, type ReactNode } from 'react'
import { DeprecationRequestForm } from './DeprecationRequestForm'
import { OperationCheck } from '../components/OperationCheck'
import { RecordHistoryToggle } from './RecordHistoryView'
import { SubmissionView } from './SubmissionView'
import { ApiError, toApiError, type ApiClient, type Role } from '../api/client'
import {
  DATA_CLASSIFICATIONS,
  DATA_CLASSIFICATION_LABELS,
  UNSET_DATA_CLASSIFICATION,
} from '../api/dataClassification'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import { EmptyState, LoadingState, PageHeader } from '../components/StatePanel'
import { CATALOG_RECORD_TYPES, RECORD_TYPE_LABELS } from '../api/recordType'
import { useNavigation, useUnsavedChangesGuard } from '../routing/navigation'
import { RouteLink } from '../routing/NavigationProvider'
import type { DraftsScreen } from '../routing/routes'
import { badge, button, callout, card, fieldLabel, note, pager } from '../ui/classes'
import {
  DEFAULT_DRAFT_ENVIRONMENT,
  DEPRECATED_STATUS,
  isEditableStatus,
  isUncertainWriteError,
  recordStatusLabel,
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
type EditorInitial = { kind: 'new'; key: string } | { kind: 'edit'; detail: DraftDetail }
type PageState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: DraftPage }
  | { kind: 'error'; error: ApiError }

type BackOptions = { force?: boolean }

const DRAFTS_LIST: DraftsScreen = { kind: 'list' }

/**
 * Publisher の下書き・申請。一覧（#/drafts）・作成（#/drafts/new）・編集（#/drafts/<レコード ID>）・
 * 申請（#/drafts/<レコード ID>/submit）を、URL のハッシュで切り替える
 */
export function RecordDraftsView({ api, roles, teams, onRelogin }: Props) {
  const { route, navigate } = useNavigation()
  const screen = route.screen === 'drafts' ? route.view : DRAFTS_LIST
  // 編集画面から申請へ進むときに渡す、保存済みの下書き。URL から直接開いたときは取得し直す
  const [savedForSubmit, setSavedForSubmit] = useState<DraftDetail | null>(null)
  const allowed = roles.includes('Publisher') && teams.length > 0

  if (!roles.includes('Publisher')) return null
  if (!allowed)
    return (
      <section aria-labelledby="drafts-heading">
        <PageHeader headingId="drafts-heading" title="下書きの管理" />
        <p className={callout.warning}>作成・編集には所属チームが必要です。管理者に連絡してください。</p>
      </section>
    )

  function go(view: DraftsScreen, options: BackOptions = {}) {
    navigate({ screen: 'drafts', view }, options)
  }
  const back = (options: BackOptions = {}) => go(DRAFTS_LIST, options)

  switch (screen.kind) {
    case 'list':
      return (
        <DraftList
          api={api}
          teams={teams}
          onCreate={() => go({ kind: 'new' })}
          onEdit={(recordId) => go({ kind: 'edit', recordId })}
          onRelogin={onRelogin}
        />
      )
    case 'new':
      return (
        <NewDraftScreen
          api={api}
          teams={teams}
          onBack={back}
          onSubmitDraft={(detail) => {
            setSavedForSubmit(detail)
            go({ kind: 'submit', recordId: detail.recordId })
          }}
          onRelogin={onRelogin}
        />
      )
    case 'edit':
      return (
        <DraftLoader key={`edit-${screen.recordId}`} api={api} recordId={screen.recordId} title="下書きを編集" onBack={back} onRelogin={onRelogin}>
          {(detail) => (
            <DraftEditor
              api={api}
              initial={{ kind: 'edit', detail }}
              teams={teams}
              onBack={back}
              onSubmitDraft={(saved) => {
                setSavedForSubmit(saved)
                go({ kind: 'submit', recordId: saved.recordId })
              }}
              onRelogin={onRelogin}
            />
          )}
        </DraftLoader>
      )
    case 'submit':
      return (
        <DraftLoader
          key={`submit-${screen.recordId}`}
          api={api}
          recordId={screen.recordId}
          title="下書きを申請"
          initial={savedForSubmit?.recordId === screen.recordId ? savedForSubmit : null}
          onBack={back}
          onRelogin={onRelogin}
        >
          {(detail) => <SubmissionView api={api} detail={detail} onBack={() => back()} onRelogin={onRelogin} />}
        </DraftLoader>
      )
  }
}

/** 所属チームの下書き対象の一覧。開くたびに 1 ページ目から読み込む */
function DraftList({
  api,
  teams,
  onCreate,
  onEdit,
  onRelogin,
}: {
  api: ApiClient
  teams: string[]
  onCreate: () => void
  onEdit: (recordId: string) => void
  onRelogin: () => void
}) {
  const [page, setPage] = useState<PageState>({ kind: 'loading' })
  const [token, setToken] = useState<string | undefined>()
  const [revision, setRevision] = useState(0)
  useEffect(() => {
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
  }, [api, token, revision])

  function reload() {
    setPage({ kind: 'loading' })
    setToken(undefined)
    setRevision((value) => value + 1)
  }

  return (
    <section aria-labelledby="drafts-heading">
      <PageHeader
        headingId="drafts-heading"
        title="下書きの管理"
        description={
          <p>
            所属チーム（<span className="font-medium text-slate-800">{teams.join('、')}</span>）の Agent（custom・A2A）・MCP
            サーバー・Skill を登録します。environment は {DEFAULT_DRAFT_ENVIRONMENT} 固定です。保存しても公開されません。
          </p>
        }
        actions={
          <button type="button" className={button.primary} onClick={onCreate}>
            下書きを作成
          </button>
        }
      />
      {page.kind === 'loading' ? (
        <LoadingState>一覧を読み込んでいます…</LoadingState>
      ) : page.kind === 'error' ? (
        <RequestErrorAlert error={page.error} onRelogin={onRelogin}>
          <button type="button" className={button.dangerOutline} onClick={reload}>
            一覧を再読み込み
          </button>
        </RequestErrorAlert>
      ) : (
        <div className="space-y-4">
          {page.page.items.length === 0 && (
            <EmptyState hint={page.page.nextToken ? undefined : '「下書きを作成」から Agent・MCP サーバー・Skill を作成できます。'}>
              {page.page.nextToken
                ? 'このページには所属チームの下書きがありません。次のページを確認してください。'
                : '所属チームの下書き対象はありません。'}
            </EmptyState>
          )}
          {page.page.items.length > 0 && (
            <div className={`${card} overflow-hidden`}>
              <div className={`hidden md:grid ${DRAFT_COLUMNS} bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600`} aria-hidden="true">
                <span className="py-3 px-4">レコード名 / 種別・所有チーム</span>
                <span className="py-3 px-4">版（最新 / 公開中）</span>
                <span className="py-3 px-4">状態</span>
                <span className="py-3 px-4 text-right">操作</span>
              </div>
              <ul className="divide-y divide-slate-200 text-sm">
                {page.page.items.map((item) => (
                  <li key={item.recordId} className="hover:bg-slate-50/70">
                    <div className={`grid grid-cols-1 ${DRAFT_COLUMNS} items-start`}>
                      <div className="py-3.5 px-4 min-w-0">
                        <h3 className="font-semibold text-slate-900 break-all">{item.displayName}</h3>
                        <p className="text-xs text-slate-500 break-all">
                          {RECORD_TYPE_LABELS[item.recordType]}・<span className="font-mono">{item.name}</span>・{item.ownerTeam}
                        </p>
                      </div>
                      <div className="px-4 md:py-3.5 text-xs font-mono">
                        <p className="text-slate-900 font-semibold">版 {item.recordVersion}</p>
                        <p className={item.publishedVersion ? 'text-emerald-700' : 'text-slate-400'}>
                          {item.publishedVersion ? `公開中 ${item.publishedVersion}` : '公開版なし'}
                        </p>
                      </div>
                      <div className="px-4 md:py-3.5 text-xs space-y-1">
                        <p>
                          <span className={item.hasDraft ? badge.neutral : badge.code}>
                            {item.hasDraft ? '下書きあり' : '下書きなし'}
                          </span>
                        </p>
                        <p className="text-slate-600">
                          AWS の処理状態 {recordStatusLabel(item.status)}
                          {item.operationId && ' / 結果確認中'}
                        </p>
                      </div>
                      <div className="py-3.5 px-4 md:text-right">
                        <button
                          type="button"
                          className={button.small}
                          disabled={item.operationId !== null || !isEditableStatus(item.status)}
                          onClick={() => onEdit(item.recordId)}
                        >
                          編集
                        </button>
                      </div>
                    </div>
                    {item.operationId && (
                      <div className="px-4 pb-4">
                        <OperationCheck
                          operationId={item.operationId}
                          load={(id) => api.getDraftOperation(id)}
                          onDone={reload}
                          onRelogin={onRelogin}
                        />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <nav className={pager} aria-label="下書きのページ">
            <button
              type="button"
              className={button.small}
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
              className={button.small}
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
        </div>
      )}
    </section>
  )
}

const DRAFT_COLUMNS = 'md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.2fr)_6rem]'

/** 作成画面。開くたびに新しい Idempotency-Key で始める */
function NewDraftScreen(props: {
  api: ApiClient
  teams: string[]
  onBack: (options?: BackOptions) => void
  onSubmitDraft: (detail: DraftDetail) => void
  onRelogin: () => void
}) {
  const [key] = useState(() => crypto.randomUUID())
  return <DraftEditor {...props} initial={{ kind: 'new', key }} />
}

type LoaderState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: DraftDetail }
  | { kind: 'error'; error: ApiError }

/** 編集・申請の画面で、対象の下書きを取得してから中身を出す。initial があれば取得しない */
function DraftLoader({
  api,
  recordId,
  title,
  initial = null,
  onBack,
  onRelogin,
  children,
}: {
  api: ApiClient
  recordId: string
  title: string
  initial?: DraftDetail | null
  onBack: (options?: BackOptions) => void
  onRelogin: () => void
  children: (detail: DraftDetail) => ReactNode
}) {
  const [state, setState] = useState<LoaderState>(() =>
    initial ? { kind: 'ready', detail: initial } : { kind: 'loading' },
  )
  const loading = state.kind === 'loading'
  useEffect(() => {
    if (!loading) return
    let active = true
    api.getDraft(recordId).then(
      (detail) => {
        if (active) setState({ kind: 'ready', detail })
      },
      (failure: unknown) => {
        if (active) setState({ kind: 'error', error: toApiError(failure, '下書きを取得できませんでした。') })
      },
    )
    return () => {
      active = false
    }
  }, [api, recordId, loading])

  if (state.kind === 'ready') return <>{children(state.detail)}</>
  return (
    <section aria-label={title} className="space-y-4">
      <DraftBreadcrumb current={title} onBack={() => onBack()} />
      {state.kind === 'loading' ? (
        <LoadingState>下書きを取得しています…</LoadingState>
      ) : (
        <RequestErrorAlert error={state.error} onRelogin={onRelogin} onRetry={() => setState({ kind: 'loading' })} />
      )}
    </section>
  )
}

/** 下書きの各画面の上部。一覧への戻り道 */
function DraftBreadcrumb({
  current,
  onBack,
  backDisabled = false,
  children,
}: {
  current: string
  onBack: () => void
  backDisabled?: boolean
  children?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <nav aria-label="パンくずリスト" className="flex items-center gap-2 text-xs text-slate-500">
        <RouteLink to={{ screen: 'drafts', view: DRAFTS_LIST }} className="hover:text-slate-900">
          下書き・申請
        </RouteLink>
        <span aria-hidden="true">/</span>
        <span className="text-slate-900 font-medium">{current}</span>
      </nav>
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <button type="button" className={button.small} onClick={onBack} disabled={backDisabled}>
          一覧に戻る
        </button>
      </div>
    </div>
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

/** 基本情報の欄に置く共通項目。残り（接続先の参照・連絡先・利用条件）は管理・公開情報の欄に置く */
const BASIC_FIELD_KEYS: ReadonlySet<string> = new Set(['name', 'displayName', 'recordVersion', 'description'])

const FORM_SECTION = `${card} p-6 space-y-4`
const FORM_SECTION_TITLE = 'text-base font-semibold text-slate-900 pb-3 border-b border-slate-200'

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
  wide = false,
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
  /** 2 列の配置で、行の幅いっぱいに置く */
  wide?: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className={`${fieldLabel} ${wide ? 'sm:col-span-2' : ''}`}>
      <span>
        {label}
        {!optional && input !== 'select' && !readOnly && <span className="text-rose-600"> *</span>}
      </span>
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
          // 長い定義（JSON・SKILL.md など）は等幅、説明文は通常の書体で表示する
          className={maxLength > 4096 ? undefined : 'font-sans text-sm'}
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

/** 基本情報（種別・descriptor の選択と、名前・表示名・版・説明） */
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
    <section className={FORM_SECTION}>
      <h2 className={FORM_SECTION_TITLE}>1. 基本情報</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <label className={fieldLabel}>
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
          {!isNew && <span className="text-[11px] font-normal text-slate-500">種別は作成後に変更できません。</span>}
        </label>
        <label className={fieldLabel}>
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
        {COMMON_TEXT_FIELDS.filter((spec) => BASIC_FIELD_KEYS.has(spec.key)).map((spec) => (
          <FieldControl
            key={spec.key}
            name={spec.key}
            label={spec.label}
            input={spec.input}
            maxLength={spec.maxLength}
            value={draft[spec.key]}
            readOnly={spec.key === 'name' && !isNew}
            wide={spec.input === 'textarea'}
            onChange={(value) => setText(spec.key, value)}
          />
        ))}
      </div>
    </section>
  )
}

/** 管理・公開情報（所有チーム・environment（dev 固定）・データ分類と、接続先の参照・連絡先・利用条件） */
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
    <section className={FORM_SECTION}>
      <h2 className={FORM_SECTION_TITLE}>2. 管理・公開情報</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <label className={fieldLabel}>
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
          {!isNew && (
            <span className="text-[11px] font-normal text-slate-500">所有チームの変更は Admin の「Registry 管理」から行います。</span>
          )}
        </label>
        <label className={fieldLabel}>
          environment
          <input name="environment" className="font-mono" value={draft.environment} readOnly />
        </label>
        <label className={fieldLabel}>
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
            {draft.dataClassification === UNSET_DATA_CLASSIFICATION && <option value={UNSET_DATA_CLASSIFICATION}>選択してください</option>}
            {DATA_CLASSIFICATIONS.map((value) => (
              <option key={value} value={value}>
                {DATA_CLASSIFICATION_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        {COMMON_TEXT_FIELDS.filter((spec) => !BASIC_FIELD_KEYS.has(spec.key)).map((spec) => (
          <FieldControl
            key={spec.key}
            name={spec.key}
            label={spec.label}
            input={spec.input}
            maxLength={spec.maxLength}
            value={draft[spec.key]}
            wide={spec.input === 'textarea'}
            onChange={(value) => onChange({ ...draft, [spec.key]: value })}
          />
        ))}
      </div>
    </section>
  )
}

/** descriptor ごとの入力項目 */
function DescriptorFields({ draft, onChange }: { draft: RecordDraft; onChange: (draft: RecordDraft) => void }) {
  const specs = DESCRIPTOR_FIELD_SPECS[draft.descriptorType]
  if (specs.length === 0) return null
  return (
    <section className={FORM_SECTION}>
      <h2 className={FORM_SECTION_TITLE}>3. {DESCRIPTOR_LABELS[draft.descriptorType]} の固有項目</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {specs.map((spec) => (
          <FieldControl
            key={spec.key}
            name={spec.key}
            label={spec.label}
            input={spec.input}
            maxLength={spec.maxLength}
            value={draft[spec.key] ?? ''}
            options={spec.options}
            optional={spec.optional}
            wide={spec.input === 'textarea'}
            onChange={(value) => onChange({ ...draft, [spec.key]: value })}
          />
        ))}
      </div>
    </section>
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
  const backDisabled = save.kind === 'saving' || (isNew && disabled && save.kind === 'error')
  return (
    <section aria-labelledby="draft-editor-heading" className="space-y-6">
      <DraftBreadcrumb current={text.title} onBack={() => onBack()} backDisabled={backDisabled}>
        {initial.kind === 'edit' && (
          <div className="w-56">
            <RecordHistoryToggle api={api} recordId={initial.detail.recordId} onRelogin={onRelogin} />
          </div>
        )}
      </DraftBreadcrumb>
      <div>
        <div className="flex flex-wrap items-center gap-2 mb-1">
          {initial.kind === 'edit' && <span className={badge.neutral}>{recordStatusLabel(initial.detail.status)}</span>}
          {savedDetail?.publishedVersion && <span className={badge.success}>公開中の版: {savedDetail.publishedVersion}</span>}
          {dirty && <span className={badge.warning}>未保存の変更あり</span>}
        </div>
        <h1 id="draft-editor-heading" className="text-2xl font-bold tracking-tight text-slate-900">
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
      {initial.kind === 'edit' && initial.detail.status === 'PENDING_APPROVAL' && (
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
            {initial.kind === 'edit' && initial.detail.status === 'PENDING_APPROVAL' &&
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
