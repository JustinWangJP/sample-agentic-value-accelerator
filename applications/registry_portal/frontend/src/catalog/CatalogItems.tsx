import type { CatalogItem } from '../api/client'

type Props = {
  items: CatalogItem[]
  onOpenDetail: (item: CatalogItem) => void
}

export function CatalogItems({ items, onOpenDetail }: Props) {
  return (
    <ul className="catalog-items">
      {items.map((item) => {
        const name = item.displayName || item.name
        return (
          <li key={item.recordId} className="catalog-item">
            <h3>{name}</h3>
            <p className="meta">
              <code>{item.name}</code>
              {item.recordVersion && <span>版 {item.recordVersion}</span>}
              {item.isA2A && <span className="badge">A2A</span>}
            </p>
            {item.description && <p>{item.description}</p>}
            <button type="button" onClick={() => onOpenDetail(item)}>
              {name}の詳細
            </button>
          </li>
        )
      })}
    </ul>
  )
}
