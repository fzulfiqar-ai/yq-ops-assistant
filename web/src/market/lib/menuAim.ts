/**
 * Menu aim (29-Oct-2026, motion K1). A hand moving diagonally from a category in the Browse menu to
 * that category's panel crosses the rows below it; without this, every row it crosses swapped the
 * panel before the hand arrived. While the pointer heads for the panel — it moved toward the panel's
 * near edge and stays inside the triangle drawn from where it was to that edge's two ends — the row
 * it enters waits (MegaNav: AIM_MS); a pointer moving down the list, or resting, switches at once.
 */

export interface Pt {
  x: number
  y: number
}

export interface Box {
  left: number
  right: number
  top: number
  bottom: number
}

/** a few px past the panel's ends, so a hand heading for its corner still counts */
const SLACK = 12

function side(p: Pt, a: Pt, b: Pt): number {
  return (p.x - b.x) * (a.y - b.y) - (a.x - b.x) * (p.y - b.y)
}

function inTriangle(p: Pt, a: Pt, b: Pt, c: Pt): boolean {
  const d1 = side(p, a, b)
  const d2 = side(p, b, c)
  const d3 = side(p, c, a)
  const neg = d1 < 0 || d2 < 0 || d3 < 0
  const pos = d1 > 0 || d2 > 0 || d3 > 0
  return !(neg && pos)
}

/**
 * The hand's last few positions over the menu, and how long it has rested. The heading is measured
 * from the oldest of the last four positions: from the previous one alone a 1-2 px step would make
 * the direction noise. (Kept here rather than in the component: the clock is read from event
 * handlers and timers, never during a render.)
 */
export function createTrail() {
  let pts: Pt[] = []
  let movedAt = 0
  return {
    move(x: number, y: number) {
      pts = [...pts.slice(-3), { x, y }]
      movedAt = performance.now()
    },
    oldest: (): Pt | undefined => pts[0],
    latest: (): Pt | undefined => pts[pts.length - 1],
    restedMs: () => performance.now() - movedAt,
  }
}

/** `prev` → `cur` is heading for `panel`. The near edge is the panel's left in English and its right in Arabic. */
export function headingForPanel(prev: Pt, cur: Pt, panel: Box, rtl = false): boolean {
  const dx = cur.x - prev.x
  if (rtl ? dx >= 0 : dx <= 0) return false
  const edge = rtl ? panel.right : panel.left
  return inTriangle(cur, prev, { x: edge, y: panel.top - SLACK }, { x: edge, y: panel.bottom + SLACK })
}
