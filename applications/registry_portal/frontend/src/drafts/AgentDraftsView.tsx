import { useEffect, useState } from 'react'
import { ApiError, toApiError, type ApiClient, type Role } from '../api/client'
import { DATA_CLASSIFICATIONS } from '../api/dataClassification'
import { RequestErrorAlert } from '../components/RequestErrorAlert'
import {
  DEFAULT_DRAFT_ENVIRONMENT,
  DRAFT_TEXT_FIELDS,
  isEditableStatus,
  isUncertainSaveError,
  settlesCreateKey,
  type AgentDraft,
  type DraftDetail,
  type DraftOperation,
  type DraftPage,
} from './api'

type Props = { api: ApiClient; roles: Role[]; teams: string[]; onRelogin: () => void }
type Screen =
  | { kind: 'list' }
  | { kind: 'new'; key: string }
  | { kind: 'edit'; detail: DraftDetail }
type PageState =
  | { kind: 'loading' }
  | { kind: 'ready'; page: DraftPage }
  | { kind: 'error'; error: ApiError }

export function AgentDraftsView({ api, roles, teams, onRelogin }: Props) {
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
        <h2>Agent 下書きの管理</h2>
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
      <h2 id="drafts-heading">Agent 下書きの管理</h2>
      <p>
        所属チームの custom Agent を登録します。environment は {DEFAULT_DRAFT_ENVIRONMENT}{' '}
        固定です。保存しても公開されません。
      </p>
      {screen.kind === 'list' ? (
        <>
          <button
            type="button"
            onClick={() => setScreen({ kind: 'new', key: crypto.randomUUID() })}
          >
            Agent 下書きを作成
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
                    : '所属チームの Agent はありません。'}
                </p>
              )}
              <ul className="catalog-items">
                {page.page.items.map((item) => (
                  <li key={item.recordId}>
                    <h3>{item.displayName}</h3>
                    <p>
                      {item.name}・{item.ownerTeam}・版 {item.recordVersion}
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
      ) : (
        <DraftEditor
          key={screen.kind === 'new' ? screen.key : screen.detail.recordId}
          api={api}
          initial={screen}
          teams={teams}
          onBack={back}
          onRelogin={onRelogin}
        />
      )}
    </section>
  )
}

function OperationCheck({
  api,
  operationId,
  onDone,
  onRelogin,
}: {
  api: ApiClient
  operationId: string
  onDone: (operation: DraftOperation) => void
  onRelogin: () => void
}) {
  const [message, setMessage] = useState('結果確認中です。確定するまで書き込みを停止しています。')
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  async function check() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.getDraftOperation(operationId)
      if (result.operationState !== 'VERIFYING') onDone(result)
      else setMessage(result.message)
    } catch (failure) {
      setError(toApiError(failure, '処理結果を取得できませんでした。'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div>
      <p role="status">{message}</p>
      <button type="button" disabled={busy} onClick={() => void check()}>
        処理結果を確認
      </button>
      {error && <RequestErrorAlert error={error} onRelogin={onRelogin} />}
    </div>
  )
}

/** 作成と編集で異なる文言 */
const EDITOR_TEXT = {
  new: {
    title: 'Agent 下書きを作成',
    uncertain:
      '作成結果が不明です。同じ入力とキーで保存を再試行してください。この画面を閉じて新しく作成すると重複する可能性があります。',
  },
  edit: {
    title: 'Agent 下書きを編集',
    uncertain:
      '更新結果が不明です。一覧に戻り、対象の「処理結果を確認」で結果を確認してください。',
  },
} as const

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'result'; operation: DraftOperation }
function DraftEditor({
  api,
  initial,
  teams,
  onBack,
  onRelogin,
}: {
  api: ApiClient
  initial: Exclude<Screen, { kind: 'list' }>
  teams: string[]
  onBack: () => void
  onRelogin: () => void
}) {
  const [draft, setDraft] = useState<AgentDraft>(() =>
    initial.kind === 'edit'
      ? initial.detail.draft
      : {
          name: '',
          displayName: '',
          description: '',
          recordVersion: '1.0.0',
          ownerTeam: teams[0] ?? '',
          environment: DEFAULT_DRAFT_ENVIRONMENT,
          dataClassification: 'private',
          purpose: '',
          connectionReference: '',
          authentication: '',
          contact: '',
          termsOfUse: '',
        },
  )
  const [hash, setHash] = useState(initial.kind === 'edit' ? initial.detail.contentHash : '')
  const [createKey, setCreateKey] = useState(initial.kind === 'new' ? initial.key : '')
  const [save, setSave] = useState<SaveState>({ kind: 'idle' })
  const isNew = initial.kind === 'new'
  const text = EDITOR_TEXT[initial.kind]
  const resultLocked = save.kind === 'result' && save.operation.operationState !== 'FAILED'
  const uncertain = save.kind === 'error' && isUncertainSaveError(save.error)
  const saveDisabled =
    save.kind === 'saving' || resultLocked || (!isNew && uncertain)
  const disabled = saveDisabled || uncertain
  const canRetryCreate =
    isNew && save.kind === 'result' && save.operation.operationState === 'VERIFYING'
  async function submit(retryCreate = false) {
    if (saveDisabled && !(retryCreate && canRetryCreate)) return
    setSave({ kind: 'saving' })
    try {
      const operation =
        initial.kind === 'new'
          ? await api.createDraft(draft, createKey)
          : await api.updateDraft(initial.detail.recordId, draft, hash)
      receiveResult(operation)
    } catch (failure) {
      const error = toApiError(failure, '下書きを保存できませんでした。')
      if (isNew && settlesCreateKey(error)) setCreateKey(crypto.randomUUID())
      setSave({ kind: 'error', error })
    }
  }
  function receiveResult(operation: DraftOperation) {
    if (isNew && operation.operationState === 'FAILED') {
      setCreateKey(crypto.randomUUID())
    }
    setSave({ kind: 'result', operation })
  }
  async function reload() {
    if (initial.kind !== 'edit') return
    setSave({ kind: 'saving' })
    try {
      const latest = await api.getDraft(initial.detail.recordId)
      setDraft(latest.draft)
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
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <fieldset disabled={disabled}>
          {DRAFT_TEXT_FIELDS.map(([key, label, maxLength]) => (
            <label key={key}>
              {label}
              {key === 'description' || key === 'purpose' || key === 'termsOfUse' ? (
                <textarea
                  name={key}
                  required
                  maxLength={maxLength}
                  value={draft[key]}
                  onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
                />
              ) : (
                <input
                  name={key}
                  required
                  maxLength={maxLength}
                  value={draft[key]}
                  readOnly={key === 'name' && !isNew}
                  onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
                />
              )}
            </label>
          ))}
          <label>
            所有チーム
            <select
              name="ownerTeam"
              value={draft.ownerTeam}
              disabled={!isNew}
              onChange={(event) => setDraft({ ...draft, ownerTeam: event.target.value })}
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
                if (classification) setDraft({ ...draft, dataClassification: classification })
              }}
            >
              {DATA_CLASSIFICATIONS.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        </fieldset>
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
          <p role="status">下書きを保存しました。公開版は変わりません。</p>
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
