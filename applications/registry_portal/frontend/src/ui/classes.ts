// 画面で共通に使う Tailwind CSS のクラス。UI プロトタイプ（ui-prototype/）の見た目を一か所に定義する。
// Tailwind はこのファイルの文字列も走査して CSS を生成するため、クラス名は動的に組み立てない

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-2 rounded-lg font-semibold transition disabled:cursor-not-allowed disabled:opacity-50'

/** ボタン。主操作・副操作・取り消せない操作・リンク風を使い分ける */
export const button = {
  primary: `${BUTTON_BASE} px-4 py-2 text-sm bg-blue-600 text-white shadow-xs hover:bg-blue-700`,
  secondary: `${BUTTON_BASE} px-4 py-2 text-sm border border-slate-300 bg-white text-slate-700 hover:bg-slate-50`,
  danger: `${BUTTON_BASE} px-4 py-2 text-sm bg-rose-600 text-white shadow-xs hover:bg-rose-700`,
  dangerOutline: `${BUTTON_BASE} px-3.5 py-1.5 text-xs border border-rose-300 bg-white text-rose-800 hover:bg-rose-100`,
  warningOutline: `${BUTTON_BASE} px-3.5 py-1.5 text-xs border border-amber-300 bg-white text-amber-900 hover:bg-amber-100`,
  small: `${BUTTON_BASE} px-3 py-1.5 text-xs border border-slate-300 bg-white text-slate-700 hover:bg-slate-50`,
  smallPrimary: `${BUTTON_BASE} px-3 py-1.5 text-xs bg-blue-600 text-white hover:bg-blue-700`,
  link: 'inline-flex items-center gap-1 text-sm font-semibold text-blue-600 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-50',
} as const

/** 白背景のカード（一覧の項目・フォームの枠） */
export const card = 'bg-white border border-slate-200 rounded-xl shadow-xs'

/** カードの内側の余白 */
export const cardBody = 'p-4 sm:p-5'

/** カード内の小見出し */
export const sectionTitle = 'text-base font-semibold text-slate-900'

/** 入力欄の見出し（label の文言） */
export const fieldLabel = 'flex flex-col gap-1.5 text-xs font-medium text-slate-600'

/** 定義リスト（dt / dd の 2 列） */
export const definitionList = 'grid grid-cols-1 sm:grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm'
export const definitionTerm = 'font-medium text-slate-500'
export const definitionValue = 'text-slate-900 break-all'

/** 補足の文言 */
export const note = 'text-xs text-slate-500 leading-relaxed'

/** 注意を促す囲み（決裁できない申請・結果確認中など） */
export const callout = {
  info: 'rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900',
  warning: 'rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900',
  danger: 'rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-900',
  success: 'rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900',
} as const

/** 状態バッジ */
export const badge = {
  neutral: 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700',
  info: 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20',
  success: 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20',
  warning: 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-600/20',
  danger: 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20',
  code: 'inline-flex items-center px-2 py-0.5 rounded bg-slate-100 text-slate-600 text-[11px] font-mono',
} as const

/** 等幅のコード表示（JSON・SKILL.md など） */
export const codeBlock =
  'max-h-96 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-800 whitespace-pre-wrap break-all'

/** ページ送り */
export const pager = 'flex flex-wrap items-center justify-center gap-3 pt-4 text-sm text-slate-600'
