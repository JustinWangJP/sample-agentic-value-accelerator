import {
  DATA_CLASSIFICATIONS,
  DATA_CLASSIFICATION_LABELS,
  UNSET_DATA_CLASSIFICATION,
} from '../api/dataClassification'
import { CATALOG_RECORD_TYPES, RECORD_TYPE_LABELS } from '../api/recordType'
import { card, fieldLabel } from '../ui/classes'
import type { RecordDraft } from './api'
import {
  COMMON_TEXT_FIELDS,
  DESCRIPTOR_FIELD_SPECS,
  DESCRIPTOR_LABELS,
  RECORD_TYPE_DESCRIPTORS,
  type FieldInput,
} from './descriptors'
import { switchKind } from './draftForm'

/** 基本情報の欄に置く共通項目。残り（接続先の参照・連絡先・利用条件）は管理・公開情報の欄に置く */
const BASIC_FIELD_KEYS: ReadonlySet<string> = new Set(['name', 'displayName', 'recordVersion', 'description'])

export const FORM_SECTION = `${card} p-6 space-y-4`
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
export function KindFields({
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
export function MetadataFields({
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
export function DescriptorFields({ draft, onChange }: { draft: RecordDraft; onChange: (draft: RecordDraft) => void }) {
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
