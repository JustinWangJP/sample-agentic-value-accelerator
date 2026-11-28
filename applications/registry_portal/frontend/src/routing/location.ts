// ルーティングが読み書きする URL のハッシュ。本番はブラウザ、テストはメモリー上の実装を使う

export type HashLocation = {
  /** 現在のハッシュ（先頭の # を含む。なければ空文字） */
  get(): string
  /** ハッシュを変える。replace なら履歴に積まない。どちらも hashchange は発火させない前提で扱う */
  set(hash: string, replace: boolean): void
  /** ブラウザの戻る・進む・アドレス欄の編集でハッシュが変わったときに呼ぶ */
  subscribe(listener: () => void): () => void
  /**
   * 再読み込み・タブを閉じる前の確認。shouldBlock が true を返すと、ブラウザ標準の確認ダイアログを出す。
   * 文言はブラウザが決めるため指定できない
   */
  onBeforeUnload(shouldBlock: () => boolean): () => void
}

type BrowserWindow = Pick<Window, 'location' | 'history' | 'addEventListener' | 'removeEventListener'>

export function browserHashLocation(win: BrowserWindow = window): HashLocation {
  return {
    get: () => win.location.hash,
    set: (hash, replace) => {
      // pushState / replaceState は hashchange を発火させない。画面の状態は呼び出し側が更新する
      if (replace) win.history.replaceState(win.history.state, '', hash)
      else win.history.pushState(null, '', hash)
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

/** テスト用。履歴の積み方と、戻る操作・再読み込みの確認を再現する */
export type MemoryHashLocation = HashLocation & {
  /** 積まれた履歴（先頭が最初の画面） */
  readonly entries: string[]
  /** ブラウザの戻る操作。ハッシュを 1 つ前に戻し、hashchange を通知する */
  back(): void
  /** アドレス欄の編集などで、ハッシュを外から変える */
  visit(hash: string): void
  /** 再読み込み・タブを閉じる操作。確認ダイアログを出す（離脱を止める）なら true */
  attemptUnload(): boolean
}

export function memoryHashLocation(initial = ''): MemoryHashLocation {
  const entries = [initial]
  let index = 0
  const listeners = new Set<() => void>()
  const unloadChecks = new Set<() => boolean>()
  const notify = () => listeners.forEach((listener) => listener())
  return {
    entries,
    get: () => entries[index],
    set: (hash, replace) => {
      if (replace) {
        entries[index] = hash
        return
      }
      entries.splice(index + 1)
      entries.push(hash)
      index = entries.length - 1
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
    visit: (hash) => {
      entries.splice(index + 1)
      entries.push(hash)
      index = entries.length - 1
      notify()
    },
    attemptUnload: () => [...unloadChecks].some((check) => check()),
  }
}
