import type { CatalogItem } from '../api/client'
import { badge } from '../ui/classes'

type Props = {
  items: CatalogItem[]
  onOpenDetail: (item: CatalogItem) => void
}

/** 更新日時（ISO 8601）の日付部分。解釈できない値はそのまま出す */
function updatedDate(updatedAt: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(updatedAt) ? updatedAt.slice(0, 10) : updatedAt
}

export function CatalogItems({ items, onOpenDetail }: Props) {
  return (
    <ul className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {items.map((item) => {
        const name = item.displayName || item.name
        return (
          <li
            key={item.recordId}
            className="bg-white border border-slate-200 rounded-xl p-5 hover:border-blue-300 hover:shadow-sm transition flex flex-col justify-between"
          >
            <div>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-base font-semibold text-slate-900 break-all">{name}</h3>
                    {item.isA2A && <span className={badge.info}>A2A</span>}
                  </div>
                  <p className="mt-0.5 text-xs font-mono text-slate-500 break-all">
                    <code>{item.name}</code>
                    {item.recordVersion && <span> · 版 {item.recordVersion}</span>}
                  </p>
                </div>
                {item.descriptorTypes.length > 0 && (
                  <span className={`${badge.code} shrink-0`}>{item.descriptorTypes.join(' / ')}</span>
                )}
              </div>
              {item.description && <p className="mt-3 text-sm text-slate-600 leading-relaxed">{item.description}</p>}
            </div>
            <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between gap-3 text-xs">
              <span className="text-slate-500">
                更新: <time className="font-mono text-slate-700">{updatedDate(item.updatedAt)}</time>
              </span>
              {/* 矢印は CSS で付ける（ボタンの名前は「〇〇の詳細」のまま） */}
              <button
                type="button"
                onClick={() => onOpenDetail(item)}
                className="font-semibold text-blue-600 hover:text-blue-700 text-right after:ml-1 after:content-['→']"
              >
                {name}の詳細
              </button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
