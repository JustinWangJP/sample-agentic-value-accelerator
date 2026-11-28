// 画面のルート（URL のハッシュ）と、その解釈・組み立て。
// ハッシュルーティングにする理由は README「開発」を参照（CloudFront の書き換えや Cognito のコールバックに依存しない）
import type { Role } from '../api/client'

/** 下書き・申請の画面。一覧・作成・編集・申請 */
export type DraftsScreen =
  | { kind: 'list' }
  | { kind: 'new' }
  | { kind: 'edit'; recordId: string }
  | { kind: 'submit'; recordId: string }

export type Route =
  | { screen: 'catalog'; recordId: string | null }
  | { screen: 'drafts'; view: DraftsScreen }
  | { screen: 'reviews'; recordId: string | null }
  | { screen: 'publication' }
  | { screen: 'administration' }
  | { screen: 'recovery' }

export type ScreenName = Route['screen']

/** ログイン直後・存在しないルートで表示する画面 */
export const DEFAULT_ROUTE: Route = { screen: 'catalog', recordId: null }

/** 画面ごとに必要な業務ロール。null は全員（Viewer を含む）。API 側でも同じ業務ロールを検査する */
export const SCREEN_REQUIRED_ROLE: Record<ScreenName, Role | null> = {
  catalog: null,
  drafts: 'Publisher',
  reviews: 'Curator',
  publication: 'Curator',
  administration: 'Admin',
  recovery: 'Admin',
}

/** 業務ロールで、その画面を開けるか */
export function canOpenScreen(screen: ScreenName, roles: readonly Role[]): boolean {
  const required = SCREEN_REQUIRED_ROLE[screen]
  return required === null || roles.includes(required)
}

/** レコード ID として受け付ける値。空や区切り文字を含む値は、存在しないルートとして扱う */
function recordIdOf(segment: string | undefined): string | null {
  if (segment === undefined || segment === '') return null
  let decoded: string
  try {
    decoded = decodeURIComponent(segment)
  } catch {
    return null
  }
  return decoded.trim() === '' || decoded.includes('/') ? null : decoded
}

/** ハッシュ（先頭の # を含んでもよい）を解釈する。解釈できなければ null */
export function parseRoute(hash: string): Route | null {
  const path = hash.replace(/^#/, '')
  if (path === '' || path === '/') return DEFAULT_ROUTE
  if (!path.startsWith('/')) return null
  const segments = path.slice(1).split('/')
  const [screen, first, second, ...rest] = segments
  if (rest.length > 0) return null
  const id = recordIdOf(first)
  switch (screen) {
    case 'catalog':
    case 'reviews':
      if (second !== undefined) return null
      if (first === undefined) return { screen, recordId: null }
      return id === null ? null : { screen, recordId: id }
    case 'drafts':
      if (first === undefined) return { screen, view: { kind: 'list' } }
      if (first === 'new') return second === undefined ? { screen, view: { kind: 'new' } } : null
      if (id === null) return null
      if (second === undefined) return { screen, view: { kind: 'edit', recordId: id } }
      return second === 'submit' ? { screen, view: { kind: 'submit', recordId: id } } : null
    case 'publication':
    case 'administration':
    case 'recovery':
      return first === undefined ? { screen } : null
    default:
      return null
  }
}

/** ルートをハッシュ（先頭の # を含む）に組み立てる */
export function formatRoute(route: Route): string {
  switch (route.screen) {
    case 'catalog':
    case 'reviews':
      return route.recordId === null
        ? `#/${route.screen}`
        : `#/${route.screen}/${encodeURIComponent(route.recordId)}`
    case 'drafts': {
      const { view } = route
      if (view.kind === 'list') return '#/drafts'
      if (view.kind === 'new') return '#/drafts/new'
      const base = `#/drafts/${encodeURIComponent(view.recordId)}`
      return view.kind === 'submit' ? `${base}/submit` : base
    }
    default:
      return `#/${route.screen}`
  }
}
