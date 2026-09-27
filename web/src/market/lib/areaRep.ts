/**
 * The checkout's area line ("New shops here are looked after by {name}") — shown ONLY when the
 * area step is what app/shop.py resolve_salesman will route the order by. Its precedence is: the
 * merchant's admin rep → Focus mapping → sticky rep → the session's rep link (session_ref) → a
 * pick at checkout → the area → the default. So the line says nothing when:
 *   * the page has a rep card, or a rep link at all (`ref` — even one whose rep keeps no public
 *     profile, so there is no card: the server still routes by that ref, never by the area);
 *   * the merchant picked a rep;
 *   * this device knows the merchant (an order placed here, a recognised phone, or "Same as last
 *     time"): a known shop may already have its own rep, which wins over the area.
 * A phone the device does not know may still belong to a shop with its own rep — the page cannot
 * tell without leaking who owns a number (recognize_phone is device-gated on purpose), which is
 * why the words speak of NEW shops, not "your" representative. Pure, for web/scripts/merchant_test.mjs.
 */
export interface AreaRepInput {
  hasRepCard: boolean
  ref: string | null | undefined
  pick: number | ''
  recognized: boolean
  known: boolean
  reuse: boolean
  area: string
  areaReps: Record<string, string> | null | undefined
}

export function areaRepName(i: AreaRepInput): string | null {
  const area = (i.area || '').trim()
  if (!area || i.hasRepCard || (i.ref || '').trim() || i.pick !== '' || i.recognized || i.known || i.reuse) return null
  return (i.areaReps || {})[area.toLowerCase()] || null
}
