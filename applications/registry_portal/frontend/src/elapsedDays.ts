// 審査待ちの一覧と公開管理で共通の、経過日数の計算。

const DAY_MS = 86_400_000

/** 滞留している依頼を見分けるための、指定の日時からの経過日数（切り捨て）。日時が読めなければ null */
export function elapsedDays(since: string, now: Date = new Date()): number | null {
  const started = Date.parse(since)
  if (Number.isNaN(started)) return null
  return Math.max(0, Math.floor((now.getTime() - started) / DAY_MS))
}
