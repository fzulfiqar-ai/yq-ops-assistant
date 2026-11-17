/**
 * A dated season the marketplace's words follow (17-Nov-2026, owner: Black Friday, from the day it went
 * live to Cyber Monday, then back by itself — no second deploy).
 *
 * Prices do not change for it, so nothing the season says is a sale, a discount or a saving
 * (docs/MARKETPLACE.md "honest merchandising"; scripts/qa/market_qa.py BANNED). It says two true things:
 *   - the clearing lines (shop.py _badges: deep stock, the ordinary trade price) are "Black Friday picks ·
 *     trade price" — stock a shop can run ITS OWN Black Friday offers on, with the margin on every card;
 *   - a themed brand card (lib/brands.ts) counts down to the day the shops' customers come, which is a
 *     date, not a deadline: nothing expires, so the card's "Ends in" chip stays off while it runs.
 *
 * The words live in i18n/en.ts / ar.ts `season`; i18n/index.ts lays them over the year-round ones for a
 * page load inside the window (withSeason), and lib/slides.ts asks seasonKicker for the brand card.
 * Outside a browser (the node test scripts) there is no page clock, so they read the year-round words;
 * the season itself is tested here with a fixed clock (web/scripts/merchant_test.mjs).
 */
import type { Strings } from '../i18n/en'

export interface Season {
  key: 'black_friday'
  /** first and last instants it runs (inclusive), epoch ms */
  from: number
  until: number
  /** midnight, Bahrain, of the day the countdown counts to */
  day: number
}

const DAY_MS = 86_400_000
/** Bahrain is UTC+3 all year (no daylight saving) */
const BAHRAIN_MS = 3 * 3_600_000

export const BLACK_FRIDAY: Season = {
  key: 'black_friday',
  from: Date.parse('2026-11-17T00:00:00+03:00'),
  until: Date.parse('2026-11-30T23:59:59+03:00'),
  day: Date.parse('2026-11-27T00:00:00+03:00'),
}

/** The season running at `now` (epoch ms), else null; no clock (null): none. */
export function seasonAt(now: number | null | undefined, season: Season = BLACK_FRIDAY): Season | null {
  return now != null && now >= season.from && now <= season.until ? season : null
}

/** Calendar days in Bahrain from `now`'s date to the season's day: 10 on 17 Nov, 0 on the day, -3 on the Monday after. */
export function daysToDay(now: number, season: Season = BLACK_FRIDAY): number {
  const date = (t: number) => Math.floor((t + BAHRAIN_MS) / DAY_MS)
  return date(season.day) - date(now)
}

/** The page's clock for the season: none outside a browser (as i18n/index.ts detect() picks English there). */
export function pageNow(): number | null {
  return typeof document === 'undefined' ? null : Date.now()
}

/** A themed brand card's kicker while the season runs ("Black Friday in 10 days"), else null. */
export function seasonKicker(t: Strings, now: number | null): string | null {
  return now != null && seasonAt(now) ? t.season.kicker(daysToDay(now)) : null
}

/** The year-round words with the season's laid over them while it runs; outside it, `base` itself. */
export function withSeason(base: Strings, now: number | null): Strings {
  if (!seasonAt(now)) return base
  const w = base.season
  return {
    ...base,
    deals: { ...base.deals, lastChance: w.picksTitle, line: w.picksLine, dealsLine: w.dealsLine, dealsLineMixed: w.dealsLineMixed, last: w.picks, badge: w.badge },
    shop: { ...base.shop, clearance: w.picks },
    slides: { ...base.slides, last: w.picksTitle, lastLine: w.slideLine, lastCta: w.slideCta },
    campaign: { ...base.campaign, categoryLast: w.categoryPicks },
  }
}
