// @vitest-environment happy-dom
// ハッシュルーティングと、未保存の変更があるときの画面遷移・戻る/進む・再読み込みの確認（Issue #85）

import { act, createElement, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { memoryHashLocation, type MemoryHashLocation } from '../routing/location'
import { useNavigation, useUnsavedChangesGuard } from '../routing/navigation'
import { DISCARD_AND_LEAVE, KEEP_EDITING, NavigationProvider, RouteLink } from '../routing/NavigationProvider'
import { formatRoute } from '../routing/routes'

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

/** 現在のルートを表示し、未保存の変更を切り替えられる確認用の画面 */
function Probe() {
  const { route, navigate } = useNavigation()
  const [dirty, setDirty] = useState(false)
  useUnsavedChangesGuard(dirty)
  return createElement(
    'div',
    null,
    createElement('p', { 'data-testid': 'route' }, formatRoute(route)),
    createElement('button', { type: 'button', onClick: () => setDirty(true) }, '入力する'),
    createElement('button', { type: 'button', onClick: () => setDirty(false) }, '保存する'),
    createElement('button', { type: 'button', onClick: () => navigate({ screen: 'reviews', recordId: null }) }, '審査へ'),
    createElement(
      'button',
      { type: 'button', onClick: () => navigate({ screen: 'reviews', recordId: null }, { force: true }) },
      '確認なしで審査へ',
    ),
    createElement(RouteLink, { to: { screen: 'recovery' } }, '回復操作へ'),
  )
}

async function mount(initial = '#/drafts/new'): Promise<MemoryHashLocation> {
  const location = memoryHashLocation(initial)
  await act(() => root.render(createElement(NavigationProvider, { location, children: createElement(Probe) })))
  return location
}

function currentRoute(): string {
  return container.querySelector('[data-testid="route"]')?.textContent ?? ''
}

function dialog(): Element | null {
  return container.querySelector('[role="dialog"]')
}

async function click(label: string) {
  const node = [...container.querySelectorAll<HTMLElement>('button, a')].find(
    (element) => element.textContent === label,
  )
  if (!node) throw new Error(`見つかりません: ${label}`)
  await act(() => node.click())
}

describe('ハッシュルーティング', () => {
  it('ハッシュのルートを表示し、画面遷移で履歴に積む', async () => {
    const location = await mount('#/catalog')
    expect(currentRoute()).toBe('#/catalog')

    await click('審査へ')
    expect(currentRoute()).toBe('#/reviews')
    expect(location.entries).toEqual(['#/catalog', '#/reviews'])
  })

  it('リンクのクリックもアプリ内の遷移として扱う', async () => {
    const location = await mount('#/catalog')
    await click('回復操作へ')
    expect(currentRoute()).toBe('#/recovery')
    expect(location.get()).toBe('#/recovery')
  })

  it('存在しないルートとハッシュなしは、カタログ一覧を表示する', async () => {
    await mount('#/unknown')
    expect(currentRoute()).toBe('#/catalog')
  })

  it('戻る操作でハッシュが変わると、画面も切り替わる', async () => {
    const location = await mount('#/catalog')
    await click('審査へ')
    await act(() => location.back())
    expect(currentRoute()).toBe('#/catalog')
  })
})

describe('未保存の変更の確認', () => {
  it('未保存の変更がなければ、確認せずに移動する', async () => {
    await mount()
    await click('審査へ')
    expect(dialog()).toBeNull()
    expect(currentRoute()).toBe('#/reviews')
  })

  it('未保存の変更があるときの画面遷移は、確認して「編集に戻る」なら移動しない', async () => {
    const location = await mount()
    await click('入力する')
    await click('審査へ')

    expect(dialog()?.textContent).toContain('保存していない変更があります。破棄して移動しますか？')
    await click(KEEP_EDITING)
    expect(dialog()).toBeNull()
    expect(currentRoute()).toBe('#/drafts/new')
    expect(location.entries).toEqual(['#/drafts/new'])
  })

  it('「破棄して移動」を選ぶと移動し、以降は確認しない', async () => {
    await mount()
    await click('入力する')
    await click('回復操作へ')
    await click(DISCARD_AND_LEAVE)

    expect(dialog()).toBeNull()
    expect(currentRoute()).toBe('#/recovery')
    await click('審査へ')
    expect(dialog()).toBeNull()
  })

  it('保存して未保存の変更がなくなれば、確認しない', async () => {
    await mount()
    await click('入力する')
    await click('保存する')
    await click('審査へ')
    expect(dialog()).toBeNull()
    expect(currentRoute()).toBe('#/reviews')
  })

  it('保存が確定した直後の遷移（force）は確認しない', async () => {
    await mount()
    await click('入力する')
    await click('確認なしで審査へ')
    expect(dialog()).toBeNull()
    expect(currentRoute()).toBe('#/reviews')
  })

  it('戻る・進むで「編集に戻る」を選ぶと、ハッシュを元の画面に戻す', async () => {
    const location = await mount('#/catalog')
    await click('審査へ')
    await click('入力する')
    await act(() => location.back())

    expect(dialog()).not.toBeNull()
    // 確認の間は画面を切り替えない
    expect(currentRoute()).toBe('#/reviews')
    await click(KEEP_EDITING)
    expect(location.get()).toBe('#/reviews')
    expect(currentRoute()).toBe('#/reviews')
  })

  it('戻る・進むで「破棄して移動」を選ぶと、戻った先の画面を表示する', async () => {
    const location = await mount('#/catalog')
    await click('審査へ')
    await click('入力する')
    await act(() => location.back())
    await click(DISCARD_AND_LEAVE)

    expect(currentRoute()).toBe('#/catalog')
    expect(location.get()).toBe('#/catalog')
  })

  it('再読み込み・タブを閉じる操作は、未保存の変更があるときだけブラウザの確認を出す', async () => {
    const location = await mount()
    expect(location.attemptUnload()).toBe(false)
    await click('入力する')
    expect(location.attemptUnload()).toBe(true)
    await click('保存する')
    expect(location.attemptUnload()).toBe(false)
  })
})

describe('ブラウザのハッシュ', () => {
  function fakeWindow(hash = '') {
    const listeners = new Map<string, EventListener>()
    const history = { state: null, pushState: vi.fn(), replaceState: vi.fn() }
    const win = {
      location: { hash },
      history,
      addEventListener: (type: string, listener: EventListener) => listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    }
    return { win: win as unknown as Window, history, listeners }
  }

  it('遷移は pushState、置き換えは replaceState で行い、hashchange を購読する', async () => {
    const { browserHashLocation } = await import('../routing/location')
    const { win, history, listeners } = fakeWindow('#/catalog')
    const location = browserHashLocation(win)
    expect(location.get()).toBe('#/catalog')

    location.set('#/reviews', false)
    location.set('#/drafts', true)
    expect(history.pushState).toHaveBeenCalledWith(null, '', '#/reviews')
    expect(history.replaceState).toHaveBeenCalledWith(null, '', '#/drafts')

    const listener = vi.fn()
    const unsubscribe = location.subscribe(listener)
    listeners.get('hashchange')?.(new Event('hashchange'))
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    expect(listeners.has('hashchange')).toBe(false)
  })

  it('再読み込みの前の確認は、止めるべきときだけ beforeunload を取り消す', async () => {
    const { browserHashLocation } = await import('../routing/location')
    const { win, listeners } = fakeWindow()
    let block = false
    browserHashLocation(win).onBeforeUnload(() => block)

    const allowed = new Event('beforeunload', { cancelable: true })
    listeners.get('beforeunload')?.(allowed)
    expect(allowed.defaultPrevented).toBe(false)

    block = true
    const blocked = new Event('beforeunload', { cancelable: true })
    listeners.get('beforeunload')?.(blocked)
    expect(blocked.defaultPrevented).toBe(true)
  })
})
