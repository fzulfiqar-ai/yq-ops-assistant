import type { ShopItem } from '@/lib/shopApi'

/**
 * The large photo views (R6): the Lightbox and the package view used to load the ORIGINAL upload —
 * up to 6.3 MB for a photo that fills at most ~1,200 device pixels (audit SPD-09). The API writes a
 * WebP rendition set beside every photo, product and package alike, at fixed paths
 * (app/catalog.py thumb_path: `thumbs/{code}-{kind}-{size}.webp`, sizes 160/320/512/1024), and the
 * payload lists the PRODUCT set as `thumb_urls`. The package set has the same name with `-package-`,
 * so it is derived here from the 512 product URL rather than adding 175 more URLs to the payload
 * every merchant downloads; the product 1024 is read from the payload when it is there and derived
 * the same way from an older payload.
 *
 * Every chain ends with the original (the package chain then walks on to the product photo and its
 * thumb, as the panel did before R6), and ProductImage / Lightbox walk on when a candidate 404s:
 * a photo whose renditions are not built yet (uploaded before R6, before the release backfill —
 * `python -m scripts.make_market_thumbs --only-missing`) costs one failed request and then shows
 * exactly what it showed before; a broken package original shows the product, not "Photo coming soon".
 */
export type PhotoKind = 'product' | 'package'
export type PhotoItem = Pick<ShopItem, 'thumb_url' | 'thumb_urls' | 'product_image_url' | 'package_image_url'>
export type LargeSize = 512 | 1024

const PRODUCT_512 = /-product-512\.webp$/

/** The WebP rendition URL for a size, or null when the item has no photo set (or no package photo). */
export function rendition(item: PhotoItem | null | undefined, kind: PhotoKind, size: LargeSize): string | null {
  const set = item?.thumb_urls
  const base = set?.['512']
  if (!base) return null
  if (kind === 'product') {
    const own = set?.[String(size)]
    if (own) return own
    return size === 512 ? base : base.replace(PRODUCT_512, '-product-1024.webp')
  }
  if (!item?.package_image_url) return null
  return base.replace(PRODUCT_512, `-package-${size}.webp`)
}

/** 1024 once the photo will be drawn over ~560 device pixels (a 2× phone, any laptop), else 512. */
export function wantSize(cssPx: number): LargeSize {
  const dpr = typeof window !== 'undefined' && window.devicePixelRatio ? window.devicePixelRatio : 1
  return cssPx * dpr > 560 ? 1024 : 512
}

/** The fallback chain for a large view: the wanted rendition, the smaller one, the original, then
 *  (package) the product photo — so a broken package file ends on the product, never on the empty tile. */
export function largePhotoChain(item: PhotoItem | null | undefined, kind: PhotoKind, cssPx: number): string[] {
  const want = wantSize(cssPx)
  const original = kind === 'package' ? item?.package_image_url : item?.product_image_url
  const chain = [rendition(item, kind, want), want === 1024 ? rendition(item, kind, 512) : null, original, kind === 'package' ? item?.product_image_url : null, item?.thumb_url]
  const out: string[] = []
  for (const s of chain) if (typeof s === 'string' && s.length > 0 && !out.includes(s)) out.push(s)
  return out
}

/** The CSS pixels a full-screen photo can fill: the short side of the viewport. */
export function screenPx(): number {
  return typeof window === 'undefined' ? 512 : Math.min(window.innerWidth, window.innerHeight)
}
