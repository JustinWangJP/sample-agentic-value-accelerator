/**
 * カタログと下書きの種別（AWS の recordType）。A2A は Agent の descriptor として扱う。
 * バックエンドの CatalogRecordType（backend/src/registry_portal/domain/catalog.py）と同じ値に揃える
 */
export const CATALOG_RECORD_TYPES = ['AGENT', 'MCP', 'SKILL'] as const
export type CatalogRecordType = (typeof CATALOG_RECORD_TYPES)[number]

/** 画面に表示する種別名。カタログと下書きで共通 */
export const RECORD_TYPE_LABELS: Record<CatalogRecordType, string> = {
  AGENT: 'Agent',
  MCP: 'MCP サーバー',
  SKILL: 'Skill',
}

export function isCatalogRecordType(value: unknown): value is CatalogRecordType {
  return CATALOG_RECORD_TYPES.some((type) => type === value)
}
