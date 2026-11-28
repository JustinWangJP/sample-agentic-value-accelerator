// 画面のルートと遷移・未保存の変更の確認を、画面の部品から使うためのフック。実体は NavigationProvider が持つ
import { createContext, useContext, useEffect } from 'react'
import type { Route } from './routes'

export type NavigateOptions = {
  /** 履歴に積まずに置き換える */
  replace?: boolean
  /** 未保存の変更の確認を出さない（保存が確定した直後の遷移など） */
  force?: boolean
}

export type Navigation = {
  route: Route
  navigate: (route: Route, options?: NavigateOptions) => void
  /** 未保存の変更があることを登録する。返した関数で解除する */
  holdUnsavedChanges: () => () => void
}

export const NavigationContext = createContext<Navigation | null>(null)

export function useNavigation(): Navigation {
  const navigation = useContext(NavigationContext)
  if (!navigation) throw new Error('NavigationProvider の内側で使用してください。')
  return navigation
}

/** 未保存の変更があるあいだ、画面遷移・戻る/進む・再読み込みの前に確認を出す */
export function useUnsavedChangesGuard(dirty: boolean) {
  const { holdUnsavedChanges } = useNavigation()
  useEffect(() => {
    if (!dirty) return
    return holdUnsavedChanges()
  }, [dirty, holdUnsavedChanges])
}

