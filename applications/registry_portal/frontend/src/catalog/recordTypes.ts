import type { CatalogRecordType } from '../api/client'

export const CATALOG_TYPES: { type: CatalogRecordType; label: string }[] = [
  { type: 'AGENT', label: 'Agent' },
  { type: 'MCP', label: 'MCP サーバー' },
  { type: 'SKILL', label: 'Skill' },
]

export function catalogTypeLabel(type: CatalogRecordType): string {
  return CATALOG_TYPES.find((entry) => entry.type === type)?.label ?? type
}
