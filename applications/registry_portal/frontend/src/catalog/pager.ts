// カタログのページ送りの状態（React に依存しない純粋な関数）
//
// AWS の nextToken は前にしか進めないため、取得したページごとの token を積んで「前のページ」を実現する。
// AWS はページが疎であることを保証しないので、0件のページでも nextToken があれば次へ進める。
import { ApiError, type CatalogPage, type CatalogRecordType } from '../api/client'

export const UNEXPECTED_CATALOG_ERROR = 'カタログの表示中に想定外のエラーが発生しました。'

export type CatalogViewState =
  | { status: 'loading' }
  | { status: 'ready'; page: CatalogPage }
  /** requiresLogin: ログインし直せば解決しうるとき（401）だけ true */
  | { status: 'error'; message: string; requiresLogin: boolean }

export type CatalogState = {
  type: CatalogRecordType
  /** 各ページを取得するときに渡す nextToken。先頭ページは undefined */
  pageTokens: (string | undefined)[]
  /** 表示中のページ番号（1始まり） */
  pageNumber: number
  view: CatalogViewState
}

const loading: CatalogViewState = { status: 'loading' }

export function initialCatalogState(type: CatalogRecordType = 'AGENT'): CatalogState {
  return { type, pageTokens: [undefined], pageNumber: 1, view: loading }
}

export function currentPageToken(state: CatalogState): string | undefined {
  return state.pageTokens[state.pageTokens.length - 1]
}

/** 種別を切り替える、または最初のページに戻る */
export function selectType(type: CatalogRecordType): CatalogState {
  return initialCatalogState(type)
}

export function received(state: CatalogState, page: CatalogPage): CatalogState {
  return { ...state, view: { status: 'ready', page } }
}

export function failed(state: CatalogState, error: unknown): CatalogState {
  const view: CatalogViewState =
    error instanceof ApiError
      ? { status: 'error', message: error.message, requiresLogin: error.requiresLogin }
      : { status: 'error', message: UNEXPECTED_CATALOG_ERROR, requiresLogin: false }
  return { ...state, view }
}

export function canGoNext(state: CatalogState): boolean {
  return state.view.status === 'ready' && state.view.page.nextToken !== null
}

export function goNext(state: CatalogState): CatalogState {
  if (state.view.status !== 'ready' || state.view.page.nextToken === null) return state
  return {
    ...state,
    pageTokens: [...state.pageTokens, state.view.page.nextToken],
    pageNumber: state.pageNumber + 1,
    view: loading,
  }
}

export function canGoPrevious(state: CatalogState): boolean {
  return state.pageTokens.length > 1
}

export function goPrevious(state: CatalogState): CatalogState {
  if (!canGoPrevious(state)) return state
  return {
    ...state,
    pageTokens: state.pageTokens.slice(0, -1),
    pageNumber: state.pageNumber - 1,
    view: loading,
  }
}

/** 同じページを読み込み直す */
export function retry(state: CatalogState): CatalogState {
  return { ...state, view: loading }
}
