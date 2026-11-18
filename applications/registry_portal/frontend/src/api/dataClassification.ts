/**
 * データ分類（custom metadata の dataClassification）。可視性は制御しない。
 * バックエンドの DataClassification と同じ値に揃える
 */
export const DATA_CLASSIFICATIONS = ['public', 'private'] as const
export type DataClassification = (typeof DATA_CLASSIFICATIONS)[number]
/**
 * データ分類の未選択。データ分類のない取り込みレコードの下書きだけが返し、保存時はサーバーが拒否する。
 * バックエンドの UNSET_DATA_CLASSIFICATION（domain/catalog.py）と同じ値に揃える
 */
export const UNSET_DATA_CLASSIFICATION = ''
export type DataClassificationChoice = DataClassification | typeof UNSET_DATA_CLASSIFICATION

export function isDataClassification(value: unknown): value is DataClassification {
  return DATA_CLASSIFICATIONS.some((classification) => classification === value)
}
