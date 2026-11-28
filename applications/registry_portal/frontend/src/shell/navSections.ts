// 左サイドバーとモバイルメニューの項目。UI プロトタイプの NAV_SECTIONS（ui-prototype/assets/prototype-model.js）に対応する
import type { Role } from '../api/client'
import { SCREEN_REQUIRED_ROLE, hasRequiredRole, type Route, type ScreenName } from '../routing/routes'

export type NavItem = { screen: ScreenName; label: string; route: Route }
export type NavSection = { title: string; requiredRole: Role | null; items: NavItem[] }

/** 画面の表示名。ページの見出しと、開けない画面の 403 表示で使う */
export const SCREEN_LABELS: Record<ScreenName, string> = {
  catalog: 'カタログ',
  drafts: '下書き・申請',
  reviews: '審査待ち',
  publication: '公開管理',
  administration: 'Registry 管理',
  recovery: '回復操作',
}

function item(screen: ScreenName, route: Route): NavItem {
  return { screen, label: SCREEN_LABELS[screen], route }
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: '発見',
    requiredRole: SCREEN_REQUIRED_ROLE.catalog,
    items: [item('catalog', { screen: 'catalog', recordId: null })],
  },
  {
    title: '登録（Publisher）',
    requiredRole: SCREEN_REQUIRED_ROLE.drafts,
    items: [item('drafts', { screen: 'drafts', view: { kind: 'list' } })],
  },
  {
    title: '審査・公開（Curator）',
    requiredRole: SCREEN_REQUIRED_ROLE.reviews,
    items: [
      item('reviews', { screen: 'reviews', recordId: null }),
      item('publication', { screen: 'publication' }),
    ],
  },
  {
    title: '管理（Admin）',
    requiredRole: SCREEN_REQUIRED_ROLE.administration,
    items: [item('administration', { screen: 'administration' }), item('recovery', { screen: 'recovery' })],
  },
]

/** 業務ロールで表示するナビの区分。必要な業務ロールのない区分は出さない */
export function visibleNavSections(roles: readonly Role[]): NavSection[] {
  return NAV_SECTIONS.filter((section) => hasRequiredRole(section.requiredRole, roles))
}
