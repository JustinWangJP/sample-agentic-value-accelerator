// カタログのページ送りの状態（React に依存しない純粋な関数）
//
// AWS の nextToken は前にしか進めないため、取得したページごとの token を積んで「前のページ」を実現する。
// AWS はページが疎であることを保証しないので、0件のページでも nextToken があれば次へ進める。
import {
  supportsA2AFilter,
  toApiError,
  type CatalogPage,
  type CatalogRecordType,
} from '../api/client'

export const UNEXPECTED_CATALOG_ERROR = 'カタログの表示中に想定外のエラーが発生しました。'

export type CatalogViewState =
  | { status: 'loading' }
  | { status: 'ready'; page: CatalogPage }
  /** requiresLogin: ログインし直せば解決しうるとき（401）だけ true */
  | { status: 'error'; message: string; requiresLogin: boolean }

export type CatalogState = {
  type: CatalogRecordType
  a2aOnly: boolean
  /** 各ページを取得するときに渡す nextToken。先頭ページは undefined */
  pageTokens: (string | undefined)[]
  /** 表示中のページ番号（1始まり） */
  pageNumber: number
  view: CatalogViewState
}

const loading: CatalogViewState = { status: 'loading' }

/** 指定した種別とフィルターの1ページ目 */
export function initialCatalogState(type: CatalogRecordType, a2aOnly = false): CatalogState {
  return {
    type,
    a2aOnly: supportsA2AFilter(type) && a2aOnly,
    pageTokens: [undefined],
    pageNumber: 1,
    view: loading,
  }
}

export function currentPageToken(state: CatalogState): string | undefined {
  return state.pageTokens[state.pageTokens.length - 1]
}

export function pageReceived(state: CatalogState, page: CatalogPage): CatalogState {
  return { ...state, view: { status: 'ready', page } }
}

export function pageFailed(state: CatalogState, error: unknown): CatalogState {
  const { message, requiresLogin } = toApiError(error, UNEXPECTED_CATALOG_ERROR)
  return { ...state, view: { status: 'error', message, requiresLogin } }
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
export function reloadPage(state: CatalogState): CatalogState {
  return { ...state, view: loading }
}
