import type { ApiClient, Role } from '../api/client'

/** 業務画面に共通で渡す値。API・業務ロール・再ログインの操作（App の CurrentScreen が組み立てる） */
export type ScreenProps<Api = ApiClient> = { api: Api; roles: readonly Role[]; onRelogin: () => void }
