import type { RepCard } from '@/lib/shopApi'
import type { UpcomingItem, UpcomingVariant } from '../lib/marketApi'
import { locale, S } from '../strings'

/**
 * What the "Coming soon" surfaces share (card, notify sheet, rail, brand page): the locale's copy
 * block, the EN/AR pick per field, the WhatsApp deep link with the model and variant prefilled,
 * and the per-phone memory of which cards were asked about. No component here, so fast refresh
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

/**
 * "earbuds, cables, chargers and screen protectors" — the category words of the cards actually
 * on the page, in shelf order, each once. Publish only the cables and the headline says cables;
 * never the whole-range list from the owner's draft copy.
 */
export function categoryPhrase(items: UpcomingItem[]): string {
  const t = upcomingCopy()
  const words: string[] = []
  for (const it of items) {
    const cat = (it.category || '').trim()
    if (!cat) continue
    const word = t.kinds[cat] || (locale.lang === 'ar' ? cat : cat.toLowerCase())
    if (!words.includes(word)) words.push(word)
  }
  return t.kindsJoin(words.length ? words : [t.kindsFallback])
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
