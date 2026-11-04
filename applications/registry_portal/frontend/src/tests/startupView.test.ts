// 起動処理の状態ごとの画面表示を、静的な HTML に描画して検証する
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { StartupView } from '../App'
import type { StartupState } from '../startup'

const render = (state: StartupState | null) => renderToStaticMarkup(createElement(StartupView, { state }))

describe('起動処理の画面表示', () => {
  it('起動処理の完了待ちとリダイレクト中は、ログイン中と表示する', () => {
    expect(render(null)).toContain('ログインしています')
    expect(render({ status: 'redirecting' })).toContain('ログインしています')
  })

  it('自分の主体・業務ロール・所属チームを表示する', () => {
    const html = render({
      status: 'ready',
      me: {
        principal: { type: 'human', issuer: 'https://issuer.example.com', subject: 'sub-1' },
        roles: ['Viewer', 'Publisher'],
        teams: ['team-payments'],
      },
    })

    expect(html).toContain('自分の主体・業務ロール・所属チーム')
    expect(html).toContain('https://issuer.example.com')
    expect(html).toContain('sub-1')
    expect(html).toContain('Viewer、Publisher')
    expect(html).toContain('team-payments')
  })

  it('所属チームがなければ、所属チームなしと表示する', () => {
    const html = render({
      status: 'ready',
      me: {
        principal: { type: 'human', issuer: 'https://issuer.example.com', subject: 'sub-1' },
        roles: ['Viewer'],
        teams: [],
      },
    })

    expect(html).toContain('所属チームなし')
  })

  it('ログインし直す操作があるときだけ、もう一度ログインするボタンを表示する', () => {
    const withRetry = render({ status: 'error', message: '認証が必要です。', retryLogin: () => {} })
    const withoutRetry = render({ status: 'error', message: 'この操作を行う権限がありません。' })

    expect(withRetry).toContain('認証が必要です。')
    expect(withRetry).toContain('もう一度ログインする')
    expect(withoutRetry).toContain('この操作を行う権限がありません。')
    expect(withoutRetry).not.toContain('もう一度ログインする')
  })
})
