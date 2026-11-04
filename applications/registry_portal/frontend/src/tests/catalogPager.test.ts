// カタログのページ送りの状態遷移を検証する
import { describe, expect, it } from 'vitest'
import { ApiError, type CatalogPage } from '../api/client'
import {
  canGoNext,
  canGoPrevious,
  currentPageToken,
  pageFailed,
  goNext,
  goPrevious,
  initialCatalogState,
  pageReceived,
  reloadPage,
} from '../catalog/pager'

const pageWith = (nextToken: string | null, ...ids: string[]): CatalogPage => ({
  items: ids.map((id) => ({
    recordId: id,
    name: id,
    displayName: id,
    description: '',
    recordType: 'AGENT',
    descriptorTypes: ['custom'],
    isA2A: false,
    recordVersion: '1.0.0',
    updatedAt: '2026-11-02T00:00:00Z',
  })),
  nextToken,
})

describe('カタログのページ送り', () => {
  it('最初は Agent の1ページ目を token なしで読み込む', () => {
    const state = initialCatalogState('AGENT')

    expect(state.type).toBe('AGENT')
    expect(state.view).toEqual({ status: 'loading' })
    expect(currentPageToken(state)).toBeUndefined()
    expect(state.pageNumber).toBe(1)
  })

  it('次のページへ進むと、前の応答の nextToken で読み込む', () => {
    let state = pageReceived(initialCatalogState('AGENT'), pageWith('token-2', 'a'))
    expect(canGoNext(state)).toBe(true)

    state = goNext(state)

    expect(state.view).toEqual({ status: 'loading' })
    expect(currentPageToken(state)).toBe('token-2')
    expect(state.pageNumber).toBe(2)
  })

  it('最後のページ（nextToken なし）と読み込み中は、次のページへ進めない', () => {
    const last = pageReceived(initialCatalogState('AGENT'), pageWith(null, 'a'))

    expect(canGoNext(last)).toBe(false)
    expect(goNext(last)).toBe(last)
    expect(canGoNext(initialCatalogState('AGENT'))).toBe(false)
  })

  it('前のページへ戻ると、そのページを取得したときの token で読み込み直す', () => {
    let state = pageReceived(initialCatalogState('AGENT'), pageWith('token-2', 'a'))
    state = pageReceived(goNext(state), pageWith('token-3', 'b'))
    state = pageReceived(goNext(state), pageWith(null, 'c'))
    expect(state.pageNumber).toBe(3)

    state = goPrevious(state)

    expect(currentPageToken(state)).toBe('token-2')
    expect(state.pageNumber).toBe(2)
    expect(canGoPrevious(goPrevious(state))).toBe(false)
  })

  it('種別を切り替えると、その種別の1ページ目に戻る', () => {
    const state = initialCatalogState('SKILL')

    expect(state.type).toBe('SKILL')
    expect(currentPageToken(state)).toBeUndefined()
    expect(state.pageNumber).toBe(1)
  })

  it('取得の失敗は日本語メッセージで表し、再試行では同じページを読み込み直す', () => {
    let state = goNext(pageReceived(initialCatalogState('AGENT'), pageWith('token-2', 'a')))

    state = pageFailed(
      state,
      new ApiError(502, 'DISCOVERY_UNAVAILABLE', 'カタログを取得できませんでした。'),
    )

    expect(state.view).toEqual({
      status: 'error',
      message: 'カタログを取得できませんでした。',
      requiresLogin: false,
    })
    state = reloadPage(state)
    expect(state.view).toEqual({ status: 'loading' })
    expect(currentPageToken(state)).toBe('token-2')
  })

  it('401 の失敗はログインし直す必要があることを示し、想定外の例外は汎用の文言にする', () => {
    const unauthenticated = pageFailed(
      initialCatalogState('AGENT'),
      new ApiError(401, 'UNAUTHENTICATED', '認証が必要です。ログインし直してください。'),
    )
    const unexpected = pageFailed(initialCatalogState('AGENT'), new Error('内部の詳細'))

    expect(unauthenticated.view).toMatchObject({ status: 'error', requiresLogin: true })
    expect(unexpected.view).toEqual({
      status: 'error',
      message: 'カタログの表示中に想定外のエラーが発生しました。',
      requiresLogin: false,
    })
  })
})
