import { CATALOG_RECORD_TYPES, type CatalogRecordType } from '../api/client'

const LABELS: Record<CatalogRecordType, string> = {
  AGENT: 'Agent',
  MCP: 'MCP サーバー',
  SKILL: 'Skill',
}

export const CATALOG_TYPES: { type: CatalogRecordType; label: string }[] = CATALOG_RECORD_TYPES.map(
  (type) => ({ type, label: LABELS[type] }),
)

export function catalogTypeLabel(type: CatalogRecordType): string {
  return LABELS[type]
}
