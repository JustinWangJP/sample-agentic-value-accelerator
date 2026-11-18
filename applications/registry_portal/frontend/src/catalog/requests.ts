// 画面の useEffect から API を呼ぶときの共通処理（React に依存しない）
import { toApiError, type ApiError } from '../api/client'

export type Settled<T> = { ok: true; value: T } | { ok: false; error: ApiError }

/**
 * promise の結果を onSettled に渡す。返した関数を呼んだ後の結果は捨てる。
 * useEffect の cleanup として返し、条件を切り替えたときに古い応答で画面を上書きしないようにする
 */
export function settleWhileActive<T>(
  promise: Promise<T>,
  fallbackMessage: string,
  onSettled: (result: Settled<T>) => void,
): () => void {
  let active = true
  promise.then(
    (value) => {
      if (active) onSettled({ ok: true, value })
    },
    (failure: unknown) => {
      if (active) onSettled({ ok: false, error: toApiError(failure, fallbackMessage) })
    },
  )
  return () => {
    active = false
  }
}
