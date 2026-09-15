import { useEffect } from 'react'
import type { ShopItem } from '@/lib/shopApi'

/**
 * Title, description and JSON-LD for the public catalog.
 *
 * The share link is pasted into WhatsApp and indexed by search engines, so the
 * page has to describe itself. Everything is restored on unmount — this is an SPA
 * and the portal pages behind the same bundle must not inherit the shop's tags.
 */

const LD_ID = 'yq-shop-jsonld'

function availability(status: ShopItem['stock_status'], allowBackorder?: boolean): string {
  if (status === 'low_stock') return 'https://schema.org/LimitedAvailability'
  if (status === 'out_of_stock') return allowBackorder ? 'https://schema.org/BackOrder' : 'https://schema.org/OutOfStock'
  return 'https://schema.org/InStock'
}

function metaTag(name: string): HTMLMetaElement {
  let el = document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute('name', name)
    document.head.appendChild(el)
  }
  return el
}

export interface SeoOptions {
  title: string
  description?: string
  /** Products to describe as an ItemList — the first 50 are used. */
  items?: ShopItem[] | null
  allowBackorder?: boolean
  currency?: string
}

export function useSeo({ title, description, items, allowBackorder, currency = 'BHD' }: SeoOptions): void {
  useEffect(() => {
    const prevTitle = document.title
    document.title = title
    return () => {
      document.title = prevTitle
    }
  }, [title])

  useEffect(() => {
    if (!description) return
    const el = metaTag('description')
    const prev = el.getAttribute('content') || ''
    el.setAttribute('content', description)
    return () => {
      el.setAttribute('content', prev)
    }
  }, [description])

  useEffect(() => {
    const list = (items || []).filter((i) => i && i.item_code).slice(0, 50)
    if (!list.length) return
    const data = {
      '@context': 'https://schema.org',
      '@type': 'ItemList',
      itemListElement: list.map((it, idx) => ({
        '@type': 'ListItem',
        position: idx + 1,
        item: {
          '@type': 'Product',
          name: it.display_name || it.item_code,
          sku: it.item_code,
          ...(it.brand ? { brand: { '@type': 'Brand', name: it.brand } } : {}),
          ...(it.product_image_url || it.thumb_url ? { image: it.product_image_url || it.thumb_url } : {}),
          ...(it.spec ? { description: String(it.spec).replace(/\s+/g, ' ').trim().slice(0, 300) } : {}),
          ...(it.price_bhd != null
            ? {
                offers: {
                  '@type': 'Offer',
                  price: Number(it.price_bhd).toFixed(3),
                  priceCurrency: currency,
                  availability: availability(it.stock_status, allowBackorder),
                },
              }
            : {}),
        },
      })),
    }
    const script = document.createElement('script')
    script.type = 'application/ld+json'
    script.id = LD_ID
    script.textContent = JSON.stringify(data)
    document.getElementById(LD_ID)?.remove()
    document.head.appendChild(script)
    return () => {
      script.remove()
    }
  }, [items, allowBackorder, currency])
}
