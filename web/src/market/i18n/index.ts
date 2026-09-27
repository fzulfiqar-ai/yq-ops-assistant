import { ar } from './ar'
import { en, plural, type Strings } from './en'

/**
 * The marketplace's language, decided ONCE per page load, before the first render:
 *
 *   ?lang=ar|en  →  the choice this phone saved (localStorage `yq-lang`)  →  the phone's first language
 *
 * public/market-lang.js applies the same rule from the <head> (an external file: the CSP forbids
 * inline scripts), so <html lang dir> are right before the first paint and no English frame flips to
 * Arabic; it writes the answer to `data-lang` and this module reads it back. Switching language
 * saves the choice and reloads (switchLang): every string is resolved at module load, including the
 * module-level tables built from `S` (nav tabs, badge and stock labels, facets), so a live swap
 * would leave half the page in the old language.
 *
 * MIRRORS public/market-lang.js pickLang() — change both together.
 */

export type Lang = 'en' | 'ar'
export type { Strings }
export { plural }

/** where the merchant's choice is kept (the boot script reads the same key) */
export const LANG_KEY = 'yq-lang'

function norm(v: string | null | undefined): Lang | null {
  const s = String(v || '')
    .trim()
    .toLowerCase()
  if (s === 'ar' || s.startsWith('ar-')) return 'ar'
  if (s === 'en' || s.startsWith('en-')) return 'en'
  return null
}

/** The rule, pure: the URL's ?lang=, then the saved choice, then the phone's FIRST language. */
export function pickLang(search: string, saved: string | null, languages: readonly string[]): Lang {
  let fromUrl: Lang | null = null
  try {
    fromUrl = norm(new URLSearchParams(search).get('lang'))
  } catch {
    /* a malformed query: ignore it */
  }
  return fromUrl || norm(saved) || (norm(languages[0]) === 'ar' ? 'ar' : 'en')
}

function detect(): Lang {
  if (typeof document === 'undefined') return 'en'
  const booted = norm(document.documentElement.getAttribute('data-lang'))
  if (booted) return booted
  let saved: string | null = null
  try {
    saved = localStorage.getItem(LANG_KEY)
  } catch {
    /* storage blocked: the URL and the phone decide */
  }
  const nav = typeof navigator !== 'undefined' ? navigator : null
  return pickLang(typeof location !== 'undefined' ? location.search : '', saved, nav ? (nav.languages?.length ? nav.languages : [nav.language]) : [])
}

const LANG: Lang = detect()

export const locale: { lang: Lang; dir: 'ltr' | 'rtl' } = { lang: LANG, dir: LANG === 'ar' ? 'rtl' : 'ltr' }

/** Every word the marketplace shows, in this page load's language. */
export const S: Strings = LANG === 'ar' ? ar : en

/**
 * Change the language: keep the choice on this phone and reload the same page in it. A `lang` in
 * the URL would win over the saved choice on the reload, so it is taken out — unless storage is
 * blocked (a private tab), where the URL is the only place the choice can live.
 */
export function switchLang(next: Lang): void {
  if (typeof window === 'undefined') return
  let saved = false
  try {
    localStorage.setItem(LANG_KEY, next)
    saved = localStorage.getItem(LANG_KEY) === next
  } catch {
    /* storage blocked */
  }
  const url = new URL(window.location.href)
  url.searchParams.delete('lang')
  if (!saved) url.searchParams.set('lang', next)
  window.location.replace(url.toString())
}

/* ───────────────────────── numbers, money, dates ─────────────────────────
   Western digits in both languages (Bahraini shops read prices in Western digits; the price book,
   the invoice and the ERP all print them), so Arabic formats with numberingSystem 'latn'. BHD is
   always quoted to 3 decimals, never grouped (1234.500, as the price book writes it). */

const LOCALE_TAG: Record<Lang, string> = { en: 'en-GB', ar: 'ar-BH-u-nu-latn' }

/** the Intl locale for dates in this language (Arabic month names, Western digits) */
export const DATE_LOCALE = LOCALE_TAG[LANG]

const moneyFormats = new Map<Lang, Intl.NumberFormat | null>()
function moneyFormat(lang: Lang): Intl.NumberFormat | null {
  if (!moneyFormats.has(lang)) {
    let f: Intl.NumberFormat | null
    try {
      f = new Intl.NumberFormat(LOCALE_TAG[lang], { minimumFractionDigits: 3, maximumFractionDigits: 3, useGrouping: false, numberingSystem: 'latn' })
    } catch {
      f = null // an engine without the option: moneyText falls back to toFixed
    }
    moneyFormats.set(lang, f)
  }
  return moneyFormats.get(lang) ?? null
}

/** 3 decimals, Western digits, no grouping: 1 → "1.000". */
export function moneyText(n: number, lang: Lang = LANG): string {
  const v = Number(n || 0)
  const f = moneyFormat(lang)
  const out = f ? f.format(v) : ''
  // anything but plain ASCII digits and one point (an engine without 'latn', a stray bidi mark) → toFixed
  return /^-?\d+\.\d{3}$/.test(out) ? out : v.toFixed(3)
}

/** the currency label beside an amount: "BHD 1.000" / «1.000 د.ب» (Arabic puts it after) */
export function withCurrency(amount: string, lang: Lang = LANG): string {
  return lang === 'ar' ? `${amount} د.ب` : `BHD ${amount}`
}

/**
 * Keep a left-to-right run (a code, a product name, a phone, "12 × 1.000") in one piece inside
 * Arabic text: the string form of <bdi dir="ltr"> (Unicode LRI … PDI), for attributes, toasts and
 * template strings where an element cannot go. English returns the text untouched.
 */
export function ltr(s: string | number): string {
  return LANG === 'ar' ? `\u2066${s}\u2069` : String(s)
}

/**
 * For an element whose whole text is an English product name that truncates or clamps: spread it
 * on the element together with the `rtl:text-right` class (ui/Ltr.tsx is the inline form). In Arabic the name then reads left to
 * right — so the ellipsis cuts the END of the name, on the right, instead of eating its first
 * words — while it still sits against the right edge like the Arabic around it. Nothing in English.
 */
export const ltrText: { dir?: 'ltr' } = LANG === 'ar' ? { dir: 'ltr' } : {}
