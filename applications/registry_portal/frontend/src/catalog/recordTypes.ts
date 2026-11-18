import { CATALOG_RECORD_TYPES, RECORD_TYPE_LABELS, type CatalogRecordType } from '../api/recordType'

export const CATALOG_TYPES: { type: CatalogRecordType; label: string }[] = CATALOG_RECORD_TYPES.map(
  (type) => ({ type, label: RECORD_TYPE_LABELS[type] }),
)

export function catalogTypeLabel(type: CatalogRecordType): string {
  return RECORD_TYPE_LABELS[type]
}
