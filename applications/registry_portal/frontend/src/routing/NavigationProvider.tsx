import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from 'react'
import { Modal } from '../components/Overlay'
import { button } from '../ui/classes'
import type { HashLocation } from './location'
import { NavigationContext, useNavigation, type Navigation, type NavigateOptions } from './navigation'
import { DEFAULT_ROUTE, formatRoute, parseRoute, type Route } from './routes'

/** 確認待ちの遷移。アプリ内の遷移は移動先を、戻る・進むはすでに変わったハッシュを持つ */
type PendingNavigation =
  | { kind: 'navigate'; target: string; replace: boolean }
  | { kind: 'history'; target: string }

export const UNSAVED_CHANGES_TITLE = '保存していない変更があります'
export const UNSAVED_CHANGES_MESSAGE = '保存していない変更があります。破棄して移動しますか？'
export const DISCARD_AND_LEAVE = '破棄して移動'
export const KEEP_EDITING = '編集に戻る'

/**
 * URL のハッシュと画面のルートを同期する。未保存の変更があるあいだは、画面遷移・戻る/進む・再読み込みの前に確認する
 */
export function NavigationProvider({ location, children }: { location: HashLocation; children: ReactNode }) {
  const [hash, setHash] = useState(() => location.get())
  const [pending, setPending] = useState<PendingNavigation | null>(null)
  const holds = useRef(new Set<symbol>())
  const currentHash = useRef(hash)
  useEffect(() => {
    currentHash.current = hash
  }, [hash])

  const go = useCallback(
    (target: string, replace: boolean) => {
      if (target !== location.get()) location.set(target, replace)
      currentHash.current = target
      setHash(target)
    },
    [location],
  )

  const navigate = useCallback(
    (route: Route, options: NavigateOptions = {}) => {
      const target = formatRoute(route)
      const replace = options.replace ?? false
      if (!options.force && holds.current.size > 0) {
        setPending({ kind: 'navigate', target, replace })
        return
      }
      go(target, replace)
    },
    [go],
  )

  useEffect(
    () =>
      location.subscribe(() => {
        const target = location.get()
        if (target === currentHash.current) return
        // 戻る・進むはすでに URL が変わっている。確認で「編集に戻る」を選んだら元に戻す
        if (holds.current.size > 0) setPending({ kind: 'history', target })
        else go(target, true)
      }),
    [location, go],
  )

  useEffect(() => location.onBeforeUnload(() => holds.current.size > 0), [location])

  const holdUnsavedChanges = useCallback(() => {
    const id = Symbol('unsaved-changes')
    holds.current.add(id)
    return () => {
      holds.current.delete(id)
    }
  }, [])

  function discardAndLeave() {
    if (!pending) return
    holds.current.clear()
    setPending(null)
    if (pending.kind === 'navigate') go(pending.target, pending.replace)
    else go(pending.target, true)
  }

  function keepEditing() {
    if (pending?.kind === 'history') location.set(currentHash.current, true)
    setPending(null)
  }

  const value = useMemo<Navigation>(
    () => ({ route: parseRoute(hash) ?? DEFAULT_ROUTE, navigate, holdUnsavedChanges }),
    [hash, navigate, holdUnsavedChanges],
  )

  return (
    <NavigationContext.Provider value={value}>
      {children}
      {pending && (
        <Modal
          title={UNSAVED_CHANGES_TITLE}
          onClose={keepEditing}
          actions={
            <>
              <button type="button" className={button.secondary} onClick={keepEditing} data-autofocus>
                {KEEP_EDITING}
              </button>
              <button type="button" className={button.danger} onClick={discardAndLeave}>
                {DISCARD_AND_LEAVE}
              </button>
            </>
          }
        >
          <p>{UNSAVED_CHANGES_MESSAGE}</p>
          <p className="text-xs text-slate-500">
            移動すると、入力中の内容は失われます。保存する場合は「{KEEP_EDITING}」を選び、下書きを保存してください。
          </p>
        </Modal>
      )}
    </NavigationContext.Provider>
  )
}

type RouteLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { to: Route }

/** ルートへのリンク。新しいタブで開く操作（修飾キー付きのクリック）はブラウザに任せる */
export function RouteLink({ to, onClick, ...props }: RouteLinkProps) {
  const { navigate } = useNavigation()
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event)
    if (event.defaultPrevented) return
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(to)
  }
  return <a href={formatRoute(to)} onClick={handleClick} {...props} />
}
