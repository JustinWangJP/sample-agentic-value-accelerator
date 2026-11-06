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

export const RECORD_TYPE_LABELS: Record<CatalogRecordType, string> = {
  AGENT: 'Agent',
  MCP: 'MCP サーバー',
  SKILL: 'Skill',
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

/** 全種別で入力する共通の文字列項目と文字数上限。バックエンドの TEXT_LIMITS と揃える */
export const COMMON_TEXT_FIELDS = [
  ['name', '名前', 255],
  ['displayName', '表示名', 255],
  ['description', '説明', 4096],
  ['recordVersion', '版（semver）', 255],
  ['connectionReference', '接続先の参照', 2048],
  ['contact', '連絡先', 1024],
  ['termsOfUse', '利用条件', 4096],
] as const

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
  /** input: 1行、textarea: 複数行、select: options から選ぶ */
  input: 'input' | 'textarea' | 'select'
  options?: readonly string[]
  /** 取得できなければ空でよい項目 */
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
    { key: 'skillCommit', label: '固定した commit', maxLength: 64, input: 'input' },
    { key: 'skillVersion', label: '固定した version', maxLength: 128, input: 'input' },
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
  dataClassification: DataClassification
  recordType: CatalogRecordType
  descriptorType: DescriptorType
  connectionReference: string
  contact: string
  termsOfUse: string
}
export type AgentDraft = CommonDraftFields & Partial<Record<SpecificFieldKey, string>>

/** descriptor ごとの入力項目の初期値 */
export function initialSpecificFields(
  descriptorType: DescriptorType,
): Partial<Record<SpecificFieldKey, string>> {
  return Object.fromEntries(
    DESCRIPTOR_FIELD_SPECS[descriptorType].map((spec) => [spec.key, spec.initial ?? '']),
  )
}
