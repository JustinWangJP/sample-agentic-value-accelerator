import { DATA_CLASSIFICATIONS, isDataClassification } from '../api/dataClassification'
import type { CatalogRecordType } from '../api/recordType'
import { DEFAULT_DRAFT_ENVIRONMENT, DRAFT_ENVIRONMENTS } from './api'
import {
  COMMON_TEXT_FIELDS,
  DESCRIPTOR_FIELD_SPECS,
  RECORD_TYPE_DESCRIPTORS,
  initialSpecificFields,
  isAllowedKind,
  isDescriptorType,
  type AgentDraft,
  type DescriptorType,
} from './descriptors'

// 下書きフォームの状態の組み立て（描画に依存しない）

/** 全種別で共通の項目名 */
const COMMON_KEYS = new Set<string>([
  ...COMMON_TEXT_FIELDS.map(([key]) => key),
  'ownerTeam',
  'environment',
  'dataClassification',
  'recordType',
  'descriptorType',
])

/** 新規作成の初期値。種別は Agent、descriptor は custom */
export function emptyDraft(ownerTeam: string): AgentDraft {
  return {
    name: '',
    displayName: '',
    description: '',
    recordVersion: '1.0.0',
    ownerTeam,
    environment: DEFAULT_DRAFT_ENVIRONMENT,
    dataClassification: 'private',
    recordType: 'AGENT',
    descriptorType: 'custom',
    connectionReference: '',
    contact: '',
    termsOfUse: '',
    ...initialSpecificFields('custom'),
  }
}

/** 種別または descriptor を切り替える。共通項目は残し、descriptor ごとの項目は初期値に戻す */
export function switchKind(
  draft: AgentDraft,
  recordType: CatalogRecordType,
  descriptorType: DescriptorType = RECORD_TYPE_DESCRIPTORS[recordType][0],
): AgentDraft {
  const common = Object.fromEntries(
    Object.entries(draft).filter(
      ([key]) =>
        COMMON_KEYS.has(key) && key !== 'recordType' && key !== 'descriptorType',
    ),
  ) as Omit<AgentDraft, 'recordType' | 'descriptorType'>
  return { ...common, recordType, descriptorType, ...initialSpecificFields(descriptorType) }
}

/** サーバーへ送る本文。現在の descriptor に属さない項目は含めない */
export function draftPayload(draft: AgentDraft): AgentDraft {
  const keys = new Set<string>([
    ...COMMON_KEYS,
    ...DESCRIPTOR_FIELD_SPECS[draft.descriptorType].map((spec) => spec.key),
  ])
  return Object.fromEntries(
    Object.entries(draft).filter(([key]) => keys.has(key)),
  ) as AgentDraft
}

export type JsonDraftResult = { draft: AgentDraft } | { error: string }

/**
 * JSON 入力を下書きとして読む。種別と descriptor の組合せ・項目名・値の型は、送る前に日本語で示す。
 * 値そのものの検証（schema version、定義の形式、秘密情報など）はサーバーが行う。
 */
export function parseDraftJson(text: string, current: AgentDraft): JsonDraftResult {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    return { error: 'JSON として読み取れません。' }
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return { error: 'JSON オブジェクトで指定してください。' }
  }
  const input = value as Record<string, unknown>
  const recordType = input.recordType ?? current.recordType
  const descriptorType = input.descriptorType ?? current.descriptorType
  if (!isDescriptorType(descriptorType) || !isAllowedKind(recordType, descriptorType)) {
    return { error: '種別と descriptor の組合せが正しくありません。' }
  }
  const allowed = new Set<string>([
    ...COMMON_KEYS,
    ...DESCRIPTOR_FIELD_SPECS[descriptorType].map((spec) => spec.key),
  ])
  for (const [key, item] of Object.entries(input)) {
    if (!allowed.has(key)) return { error: `${key} は指定できない項目です。` }
    if (typeof item !== 'string') return { error: `${key} は文字列で指定してください。` }
  }
  if (input.environment !== undefined && !DRAFT_ENVIRONMENTS.some((e) => e === input.environment)) {
    return { error: `environment は ${DRAFT_ENVIRONMENTS.join(' または ')} のみ指定できます。` }
  }
  if (input.dataClassification !== undefined && !isDataClassification(input.dataClassification)) {
    return { error: `データ分類は ${DATA_CLASSIFICATIONS.join(' または ')} を指定してください。` }
  }
  const base = switchKind(current, recordType as CatalogRecordType, descriptorType)
  return { draft: { ...base, ...input } as AgentDraft }
}
