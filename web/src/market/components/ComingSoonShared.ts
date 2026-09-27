import type { RepCard } from '@/lib/shopApi'
import type { UpcomingItem, UpcomingVariant } from '../lib/marketApi'
import { scrollMotion } from '../shell/useViewport'
import { locale, S } from '../strings'

/**
 * What the "Coming soon" surfaces share (card, notify sheet, rail, brand page): the locale's copy
 * block, the EN/AR pick per field, the section labels and fact pills, the disc pictures for the
 * night stage, the WhatsApp deep links (one model and variant, or the whole range), and the
 * per-phone memory of which cards were asked about. No component here, so fast refresh
 * stays happy and the card and the sheet do not import each other.
 */

/** the EN or AR copy block for the current locale */
export function upcomingCopy() {
  return locale.lang === 'ar' ? S.upcoming.ar : S.upcoming.en
}

export function upcomingName(it: UpcomingItem): string {
  return (locale.lang === 'ar' && it.name_ar) || it.name_en
}

export function upcomingSpec(it: UpcomingItem): string {
  return ((locale.lang === 'ar' && it.spec_ar) || it.spec_en || '').trim()
}

export function upcomingWhen(it: UpcomingItem): string {
  return ((locale.lang === 'ar' && it.expected_label_ar) || it.expected_label_en || '').trim()
}

export function variantLabel(v: UpcomingVariant): string {
  return (locale.lang === 'ar' && v.label_ar) || v.label
}

/** a section's heading and circle label: "Cables", "Wireless audio" — the category's own name when it has no entry; never a count */
export function sectionLabel(cat: string | null | undefined): string {
  const c = (cat || '').trim()
  return upcomingCopy().sections[c] || c
}

/** the section's element id on the brand page: "wk-data-cables" */
export function sectionId(cat: string | null | undefined): string {
  const slug = (cat || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return `wk-${slug || 'all'}`
}

/**
 * A button's jump to a section of this page: scrollIntoView (smooth unless reduced motion), then
 * keyboard focus on the section's heading. Never a #hash link — the URL (and a rep's ?ref=) stays
 * exactly as it came in. The section's scroll-margin keeps it clear of the pinned header.
 */
export function jumpTo(id: string): void {
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ behavior: scrollMotion(), block: 'start' })
  el.querySelector<HTMLElement>('[data-jump-focus]')?.focus({ preventScroll: true })
}

/**
 * The longest pill the narrowest card row holds whole (~134 px of row at a 360 px phone, ~6 px a
 * character at the chip's 2xs semibold): a longer fact never becomes a pill — it would end in an
 * ellipsis — and stays in the notify sheet's full spec line instead.
 */
const PILL_MAX = 18

/**
 * The card's one row of fact pills, each a whole short fact:
 *   1. the name's own differentiator, when it ends in one — "(Gen 7)", "(USB-A to USB-C)",
 *      "(Privacy)" — so four earbuds whose clamped names read alike still tell apart at 360;
 *   2. the supplier spec, split at its "·" and at " + " ("Braided + TPE + aluminium alloy" is three
 *      facts, each true alone) — never at "/", which would split "USB-A / USB-C to USB-C / Lightning"
 *      into halves that misstate the cable;
 *   3. the variant labels (colours, connectors, sizes).
 * The row is clipped to one line, so only the pills that fit show; nothing longer than PILL_MAX is
 * offered, so not even the first pill can ellipsize.
 */
export function specPills(it: UpcomingItem): string[] {
  const out: string[] = []
  const push = (s: string) => {
    const v = s.trim()
    if (v && v.length <= PILL_MAX && !out.includes(v)) out.push(v)
  }
  const tail = /\(([^()]+)\)\s*$/.exec(upcomingName(it))
  if (tail) push(tail[1])
  upcomingSpec(it)
    .split('·')
    .flatMap((part) => part.split(' + '))
    .forEach(push)
  it.variants.forEach((v) => push(variantLabel(v)))
  return out
}

/** A disc-ready picture for ComposedCreative / ProductImage: the model's product photo or its box photo (both carry a WebP size set). */
export function asCreative(it: UpcomingItem, kind: 'photo' | 'box') {
  return {
    item_code: `wk:${it.id}`,
    thumb_urls: (kind === 'box' ? it.box_thumb_urls : it.photo_thumb_urls) || null,
    product_image_url: (kind === 'box' ? it.box_url : it.photo_url) || null,
  }
}

const hasPhoto = (it: UpcomingItem) => Boolean(it.photo_url || it.photo_thumb_urls?.['320'])

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

/** "Hello Furqan, I am interested in the WEKOME range when it arrives…" — the rep's own number, for the whole range (the desktop tile) */
export function askRangeUrl(rep: RepCard | null, brand: string): string | null {
  if (!rep?.whatsapp_url) return null
  const text = upcomingCopy().askRangeText(rep.first_name || '', brand)
  return `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(text)}`
}

/** the WhatsApp deep link with the model (and the picked variant) prefilled — the rep's own number, as every other card does it */
export function askRepUrl(rep: RepCard | null, item: UpcomingItem, variant: string | null): string | null {
  if (!rep?.whatsapp_url) return null
  const text = upcomingCopy().askText(rep.first_name || '', item.brand, item.model_code, item.name_en, variant || '')
  return `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(text)}`
}

/* which cards this phone already asked about — a convenience, the server dedupes per device anyway */
const NOTIFIED_KEY = 'yq-upcoming-notified'

export function readNotified(): Set<number> {
  try {
    const raw = JSON.parse(localStorage.getItem(NOTIFIED_KEY) || '[]') as unknown
    return new Set(Array.isArray(raw) ? raw.map(Number).filter((n) => Number.isFinite(n)) : [])
  } catch {
    return new Set()
  }
}

export function rememberNotified(id: number): void {
  try {
    const s = readNotified()
    s.add(id)
    localStorage.setItem(NOTIFIED_KEY, JSON.stringify([...s].slice(-200)))
  } catch {
    /* private mode */
  }
}
