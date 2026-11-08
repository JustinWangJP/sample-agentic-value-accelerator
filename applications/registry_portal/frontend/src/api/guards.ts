// API 応答の検査に使う共通の型ガード。各 API モジュール（client.ts・drafts/api.ts・reviews/api.ts）が共有する

/** 配列や null ではない JSON オブジェクトか */
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 指定したキーの値がすべて文字列か */
export function hasStrings(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof value[key] === 'string')
}
