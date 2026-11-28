// ルーティングが読み書きする URL のハッシュ。本番はブラウザ、テストはメモリー上の実装を使う

export type HashLocation = {
  /** 現在のハッシュ（先頭の # を含む。なければ空文字） */
  get(): string
  /** ハッシュを変える。replace なら履歴に積まない。どちらも hashchange は発火させない前提で扱う */
  set(hash: string, replace: boolean): void
  /** ブラウザの戻る・進む・アドレス欄の編集で変わったハッシュを確定する */
  commit(): void
  /** ブラウザの戻る・進む・アドレス欄の編集を取り消し、元の履歴位置とハッシュに戻す */
  revert(hash: string): void
  /** ブラウザの戻る・進む・アドレス欄の編集でハッシュが変わったときに呼ぶ */
  subscribe(listener: () => void): () => void
  /**
   * 再読み込み・タブを閉じる前の確認。shouldBlock が true を返すと、ブラウザ標準の確認ダイアログを出す。
   * 文言はブラウザが決めるため指定できない
   */
  onBeforeUnload(shouldBlock: () => boolean): () => void
}

type BrowserWindow = Pick<Window, 'location' | 'history' | 'addEventListener' | 'removeEventListener'>

/** history.state に記録する位置のキー。戻る・進むのキャンセル時に元の履歴位置へ戻すために使う */
const NAV_INDEX_KEY = '__registryPortalNavIndex'

function readNavIndex(state: unknown): number | null {
  if (typeof state !== 'object' || state === null) return null
  const value = (state as Record<string, unknown>)[NAV_INDEX_KEY]
  return typeof value === 'number' && Number.isInteger(value) ? value : null
}

function withNavIndex(state: unknown, index: number): Record<string, unknown> {
  const base = typeof state === 'object' && state !== null ? (state as Record<string, unknown>) : {}
  return { ...base, [NAV_INDEX_KEY]: index }
}

export function browserHashLocation(win: BrowserWindow = window): HashLocation {
  const initialIndex = readNavIndex(win.history.state)
  let settledIndex = initialIndex ?? 0
  if (initialIndex === null) {
    // 初期エントリにも位置を付けておき、アドレス欄直接入力（state が null）と区別する
    win.history.replaceState(withNavIndex(win.history.state, settledIndex), '')
  }
  return {
    get: () => win.location.hash,
    set: (hash, replace) => {
      // pushState / replaceState は hashchange を発火させない。画面の状態は呼び出し側が更新する
      if (replace) {
        win.history.replaceState(withNavIndex(win.history.state, settledIndex), '', hash)
        return
      }
      settledIndex += 1
      win.history.pushState(withNavIndex(null, settledIndex), '', hash)
    },
    commit: () => {
      const current = readNavIndex(win.history.state)
      if (current !== null) {
        settledIndex = current
        return
      }
      settledIndex += 1
      win.history.replaceState(withNavIndex(win.history.state, settledIndex), '')
    },
    revert: (hash) => {
      const current = readNavIndex(win.history.state)
      // state に位置がなければアドレス欄の直接編集で積まれた新規エントリなので 1 つ戻す
      const delta = current !== null ? settledIndex - current : -1
      if (delta !== 0) {
        win.history.go(delta)
        return
      }
      if (win.location.hash !== hash) {
        settledIndex += 1
        win.history.pushState(withNavIndex(null, settledIndex), '', hash)
      }
    },
    subscribe: (listener) => {
      win.addEventListener('hashchange', listener)
      return () => win.removeEventListener('hashchange', listener)
    },
    onBeforeUnload: (shouldBlock) => {
      const handler = (event: BeforeUnloadEvent) => {
        if (!shouldBlock()) return
        event.preventDefault()
        // 古いブラウザは returnValue の設定で確認ダイアログを出す
        event.returnValue = ''
      }
      win.addEventListener('beforeunload', handler)
      return () => win.removeEventListener('beforeunload', handler)
    },
  }
}

/** テスト用。履歴の積み方と、戻る・進む操作・再読み込みの確認を再現する */
export type MemoryHashLocation = HashLocation & {
  /** 積まれた履歴（先頭が最初の画面） */
  readonly entries: string[]
  /** ブラウザの戻る操作。ハッシュを 1 つ前に戻し、hashchange を通知する */
  back(): void
  /** ブラウザの進む操作。ハッシュを 1 つ先に進め、hashchange を通知する */
  forward(): void
  /** アドレス欄の編集などで、ハッシュを外から変える */
  visit(hash: string): void
  /** 再読み込み・タブを閉じる操作。確認ダイアログを出す（離脱を止める）なら true */
  attemptUnload(): boolean
}

export function memoryHashLocation(initial = ''): MemoryHashLocation {
  const entries = [initial]
  let index = 0
  let settledIndex = 0
  const listeners = new Set<() => void>()
  const unloadChecks = new Set<() => boolean>()
  const notify = () => listeners.forEach((listener) => listener())
  return {
    entries,
    get: () => entries[index],
    set: (hash, replace) => {
      if (replace) {
        entries[index] = hash
        settledIndex = index
        return
      }
      entries.splice(index + 1)
      entries.push(hash)
      index = entries.length - 1
      settledIndex = index
    },
    commit: () => {
      settledIndex = index
    },
    revert: (hash) => {
      if (settledIndex >= 0 && settledIndex < entries.length && settledIndex !== index) {
        index = settledIndex
        notify()
        return
      }
      if (entries[index] !== hash) {
        entries.splice(index + 1)
        entries.push(hash)
        index = entries.length - 1
        settledIndex = index
      }
    },
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    onBeforeUnload: (shouldBlock) => {
      unloadChecks.add(shouldBlock)
      return () => unloadChecks.delete(shouldBlock)
    },
    back: () => {
      if (index === 0) return
      index -= 1
      notify()
    },
    forward: () => {
      if (index >= entries.length - 1) return
      index += 1
      notify()
    },
    visit: (hash) => {
      entries.splice(index + 1)
      entries.push(hash)
      index = entries.length - 1
      notify()
    },
    attemptUnload: () => [...unloadChecks].some((check) => check()),
  }
}
