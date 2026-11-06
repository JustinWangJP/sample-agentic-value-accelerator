/**
 * カタログと下書きの種別（AWS の recordType）。A2A は Agent の descriptor として扱う。
 * バックエンドの CatalogRecordType（backend/src/registry_portal/domain/catalog.py）と同じ値に揃える
 */
export const CATALOG_RECORD_TYPES = ['AGENT', 'MCP', 'SKILL'] as const
export type CatalogRecordType = (typeof CATALOG_RECORD_TYPES)[number]

export function isCatalogRecordType(value: unknown): value is CatalogRecordType {
  return CATALOG_RECORD_TYPES.some((type) => type === value)
}
