/**
 * データ分類（custom metadata の dataClassification）。可視性は制御しない。
 * バックエンドの DataClassification と同じ値に揃える
 */
export const DATA_CLASSIFICATIONS = ['public', 'private'] as const
export type DataClassification = (typeof DATA_CLASSIFICATIONS)[number]

export function isDataClassification(value: unknown): value is DataClassification {
  return DATA_CLASSIFICATIONS.some((classification) => classification === value)
}
