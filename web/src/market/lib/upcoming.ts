import { useEffect, useState } from 'react'
import { getUpcoming, type UpcomingItem, type UpcomingPayload } from './marketApi'

/**
 * The "Coming soon" payload (/public/market/upcoming), fetched ONCE for every surface that shows
 * it — the home slide (lib/slides 'd:soon'), the aside Spotlight, the home rail and the brand page:
 * one request in flight at a time, the answer kept in memory for the API's own cache window (60 s),
 * and a stale answer shown at once while a newer one is fetched (a page revisited after a minute
 * never loses its teaser for a frame). A failure is never cached, so the next caller — the brand
 * page's retry — asks again.
 *
 * Plus the two pure art helpers the stage, the tile and the slide share (components/ComingSoonShared
 * re-exports them): a disc picture from a card, and the stage's three discs.
 */

/** the API and the CDN cache the payload 60 s */
const TTL_MS = 60000

let cached: { at: number; data: UpcomingPayload } | null = null
let inflight: Promise<UpcomingPayload> | null = null

const fresh = () => cached !== null && Date.now() - cached.at < TTL_MS

/** The last payload this page load received, fresh or not (undefined before the first answer). */
export function cachedUpcoming(): UpcomingPayload | undefined {
  return cached?.data
}

/** The payload: from memory while it is fresh, else the one request already on its way, else a new one. */
export function fetchUpcoming(): Promise<UpcomingPayload> {
  if (cached && fresh()) return Promise.resolve(cached.data)
  if (!inflight) {
    inflight = getUpcoming().then(
      (data) => {
        cached = { at: Date.now(), data }
        inflight = null
        return data
      },
      (err: unknown) => {
        inflight = null
        throw err
      },
    )
  }
  return inflight
}

/** what a teaser may show: a switched-on payload with at least one card, else null */
function announced(d: UpcomingPayload | undefined): UpcomingPayload | null | undefined {
  if (d === undefined) return undefined
  return d.enabled && d.items.length > 0 ? d : null
}

/**
 * The announcement for a teaser surface (the slide, the Spotlight, the rail):
 *   undefined  the first answer is still on its way
 *   null       nothing to announce — switched off, no cards, or the request failed
 * `enabled` false defers the request (Home and the Spotlight wait for the catalog, so the first
 * screen's own requests go first).
 */
export function useUpcoming(enabled = true): UpcomingPayload | null | undefined {
  const [data, setData] = useState<UpcomingPayload | null | undefined>(() => announced(cachedUpcoming()))
  useEffect(() => {
    if (!enabled || fresh()) return
    let alive = true
    fetchUpcoming().then(
      (d) => {
        if (alive) setData(announced(d))
      },
      () => {
        // a failed refresh keeps what was already shown; with nothing shown there is nothing to announce
        if (alive) setData((was) => (was === undefined ? null : was))
      },
    )
    return () => {
      alive = false
    }
  }, [enabled])
  return data
}

/* ───────────────────────── art (stage, tile, slide) ───────────────────────── */

const hasPhoto = (it: UpcomingItem) => Boolean(it.photo_url || it.photo_thumb_urls?.['320'])

/** A disc-ready picture for ComposedCreative / ProductImage: the model's product photo or its box photo (both carry a WebP size set). Never a ShopItem: no price, no stock. */
export function asCreative(it: UpcomingItem, kind: 'photo' | 'box') {
  return {
    item_code: `wk:${it.id}`,
    thumb_urls: (kind === 'box' ? it.box_thumb_urls : it.photo_thumb_urls) || null,
    product_image_url: (kind === 'box' ? it.box_url : it.photo_url) || null,
  }
}

/** The night stage's three discs: the first photo of three DIFFERENT categories (earbuds, a cable, a charger — not three cables), topped up in page order when fewer categories exist. */
export function stageArt(items: UpcomingItem[]) {
  const picked: UpcomingItem[] = []
  const cats = new Set<string>()
  for (const it of items) {
    if (picked.length >= 3) break
    const c = (it.category || '').trim()
    if (!hasPhoto(it) || cats.has(c)) continue
    cats.add(c)
    picked.push(it)
  }
  for (const it of items) {
    if (picked.length >= 3) break
    if (hasPhoto(it) && !picked.includes(it)) picked.push(it)
  }
  return picked.map((it) => asCreative(it, 'photo'))
}
