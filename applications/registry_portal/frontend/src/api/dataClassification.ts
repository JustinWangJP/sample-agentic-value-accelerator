/**
 * データ分類（custom metadata の dataClassification）。可視性は制御しない。
 * バックエンドの DataClassification と同じ値に揃える
 */
export const DATA_CLASSIFICATIONS = ['public', 'private'] as const
export type DataClassification = (typeof DATA_CLASSIFICATIONS)[number]
/**
 * データ分類の表示ラベル。送信する値（DATA_CLASSIFICATIONS）は変えず、選択肢と表示にだけ補足を付ける。
 * UI プロトタイプ（ui-prototype/assets/prototype-model.js の DATA_CLASSIFICATIONS）と同じ文言に揃える
 */
export const DATA_CLASSIFICATION_LABELS: Record<DataClassification, string> = {
  public: 'public（組織外公開可）',
  private: 'private（組織内限定）',
}
/**
 * データ分類の未選択。データ分類のない取り込みレコードの下書きだけが返し、保存時はサーバーが拒否する。
 * バックエンドの UNSET_DATA_CLASSIFICATION（domain/catalog.py）と同じ値に揃える
 */
export const UNSET_DATA_CLASSIFICATION = ''
export type DataClassificationChoice = DataClassification | typeof UNSET_DATA_CLASSIFICATION

export function isDataClassification(value: unknown): value is DataClassification {
  return DATA_CLASSIFICATIONS.some((classification) => classification === value)
}
