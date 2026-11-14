import type { DataClassification } from '../api/dataClassification'
import { CATALOG_RECORD_TYPES, type CatalogRecordType } from '../api/recordType'
import type { DraftEnvironment } from './api'

// 種別（recordType）と primary descriptor の対応、descriptor ごとの入力項目。
// バックエンドの domain/descriptors.py と値を揃える。

export const DESCRIPTOR_TYPES = [
  'custom',
  'a2aAgentCard',
  'mcpServer',
  'agentSkillsDefinition',
] as const
export type DescriptorType = (typeof DESCRIPTOR_TYPES)[number]
export function isDescriptorType(value: unknown): value is DescriptorType {
  return DESCRIPTOR_TYPES.some((type) => type === value)
}

/** 種別ごとに受け付ける primary descriptor。先頭を既定とする */
export const RECORD_TYPE_DESCRIPTORS: Record<CatalogRecordType, readonly DescriptorType[]> = {
  AGENT: ['custom', 'a2aAgentCard'],
  MCP: ['mcpServer'],
  SKILL: ['agentSkillsDefinition'],
}
export function isAllowedKind(recordType: unknown, descriptorType: unknown): boolean {
  return CATALOG_RECORD_TYPES.some(
    (type) => type === recordType && RECORD_TYPE_DESCRIPTORS[type].some((d) => d === descriptorType),
  )
}

export const DESCRIPTOR_LABELS: Record<DescriptorType, string> = {
  custom: 'custom（フォームで入力）',
  a2aAgentCard: 'A2A（Agent Card）',
  mcpServer: 'MCP サーバー（server 定義と tools 定義）',
  agentSkillsDefinition: 'Skill（SKILL.md と配布物の参照）',
}

/** descriptor の dataSchemaVersion として受け付ける値（AWS の公式資料に記載の版） */
export const A2A_SCHEMA_VERSIONS = ['0.3'] as const
export const MCP_SERVER_SCHEMA_VERSIONS = [
  '2025-12-11',
  '2025-10-17',
  '2025-10-11',
  '2025-09-29',
  '2025-09-16',
  '2025-07-09',
] as const
export const MCP_TOOLS_SCHEMA_VERSIONS = [
  '2025-11-25',
  '2025-06-18',
  '2025-03-26',
  '2024-11-05',
] as const
export const SKILL_SCHEMA_VERSIONS = ['0.1.0'] as const

/** 入力欄の種類。input: 1行、textarea: 複数行、select: options から選ぶ */
export type FieldInput = 'input' | 'textarea' | 'select'

/** 全種別で入力する共通の文字列項目・文字数上限・入力欄の種類。バックエンドの TEXT_LIMITS と揃える */
export const COMMON_TEXT_FIELDS = [
  { key: 'name', label: '名前', maxLength: 255, input: 'input' },
  { key: 'displayName', label: '表示名', maxLength: 255, input: 'input' },
  { key: 'description', label: '説明', maxLength: 4096, input: 'textarea' },
  { key: 'recordVersion', label: '版（semver）', maxLength: 255, input: 'input' },
  { key: 'connectionReference', label: '接続先の参照', maxLength: 2048, input: 'input' },
  { key: 'contact', label: '連絡先', maxLength: 1024, input: 'input' },
  { key: 'termsOfUse', label: '利用条件', maxLength: 4096, input: 'textarea' },
] as const satisfies readonly { key: string; label: string; maxLength: number; input: FieldInput }[]

export type SpecificFieldKey =
  | 'purpose'
  | 'authentication'
  | 'schemaVersion'
  | 'definition'
  | 'toolsSchemaVersion'
  | 'toolsDefinition'
  | 'skillRepositoryUrl'
  | 'skillRepositorySource'
  | 'skillCommit'
  | 'skillVersion'
  | 'skillDigest'
  | 'skillMd'

export type FieldSpec = {
  key: SpecificFieldKey
  label: string
  maxLength: number
  input: FieldInput
  options?: readonly string[]
  /** 空でよい項目（ほかの項目との組合せはサーバーが検査する） */
  optional?: boolean
  /** 新規作成時の初期値。省略すると空 */
  initial?: string
}

/** descriptor ごとの入力項目。バックエンドの DESCRIPTOR_FIELDS と揃える */
export const DESCRIPTOR_FIELD_SPECS: Record<DescriptorType, readonly FieldSpec[]> = {
  custom: [
    { key: 'purpose', label: '目的', maxLength: 4096, input: 'textarea' },
    { key: 'authentication', label: '認証方式', maxLength: 1024, input: 'input' },
  ],
  a2aAgentCard: [
    {
      key: 'schemaVersion',
      label: 'Agent Card の schema version',
      maxLength: 32,
      input: 'select',
      options: A2A_SCHEMA_VERSIONS,
      initial: A2A_SCHEMA_VERSIONS[0],
    },
    { key: 'definition', label: 'Agent Card（JSON）', maxLength: 24000, input: 'textarea' },
  ],
  mcpServer: [
    {
      key: 'schemaVersion',
      label: 'server 定義の schema version',
      maxLength: 32,
      input: 'select',
      options: MCP_SERVER_SCHEMA_VERSIONS,
      initial: MCP_SERVER_SCHEMA_VERSIONS[0],
    },
    { key: 'definition', label: 'server 定義（JSON）', maxLength: 24000, input: 'textarea' },
    {
      key: 'toolsSchemaVersion',
      label: 'tools 定義の schema version',
      maxLength: 32,
      input: 'select',
      options: MCP_TOOLS_SCHEMA_VERSIONS,
      initial: MCP_TOOLS_SCHEMA_VERSIONS[0],
    },
    { key: 'toolsDefinition', label: 'tools 定義（JSON）', maxLength: 24000, input: 'textarea' },
  ],
  agentSkillsDefinition: [
    {
      key: 'schemaVersion',
      label: 'Skill の schema version',
      maxLength: 32,
      input: 'select',
      options: SKILL_SCHEMA_VERSIONS,
      initial: SKILL_SCHEMA_VERSIONS[0],
    },
    { key: 'skillRepositoryUrl', label: 'リポジトリの URL', maxLength: 2048, input: 'input' },
    {
      key: 'skillRepositorySource',
      label: 'リポジトリの種類',
      maxLength: 64,
      input: 'input',
      initial: 'github',
    },
    // commit と version は少なくとも一方を入力する（バックエンドの OPTIONAL_FIELDS と _check_skill_pin）
    {
      key: 'skillCommit',
      label: '固定した commit（version と少なくとも一方）',
      maxLength: 64,
      input: 'input',
      optional: true,
    },
    {
      key: 'skillVersion',
      label: '固定した version（例 1.2.0。commit と少なくとも一方）',
      maxLength: 128,
      input: 'input',
      optional: true,
    },
    {
      key: 'skillDigest',
      label: 'digest（取得できれば）',
      maxLength: 71,
      input: 'input',
      optional: true,
    },
    { key: 'skillMd', label: 'SKILL.md', maxLength: 32000, input: 'textarea' },
  ],
}

export type CommonDraftFields = {
  name: string
  displayName: string
  description: string
  recordVersion: string
  ownerTeam: string
  environment: DraftEnvironment
  /** 空文字は未選択。データ分類のない取り込みレコードだけが返し、保存時はサーバーが拒否する */
  dataClassification: DataClassification | ''
  recordType: CatalogRecordType
  descriptorType: DescriptorType
  connectionReference: string
  contact: string
  termsOfUse: string
}
export type RecordDraft = CommonDraftFields & Partial<Record<SpecificFieldKey, string>>

/** descriptor ごとの入力項目の初期値 */
export function initialSpecificFields(
  descriptorType: DescriptorType,
): Partial<Record<SpecificFieldKey, string>> {
  return Object.fromEntries(
    DESCRIPTOR_FIELD_SPECS[descriptorType].map((spec) => [spec.key, spec.initial ?? '']),
  )
}
