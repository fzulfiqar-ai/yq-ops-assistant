import type { ReactNode } from 'react'
import { locale } from '../strings'

/**
 * A left-to-right island in Arabic text — a code, a phone, an order number, a URL, "12 × 1.000" —
 * as <bdi dir="ltr">: it keeps its own order and never reorders its neighbours. An English page
 * renders the children as they are (no element, so no layout, selector or flex-item change there).
 * For strings in attributes and template text, i18n's ltr() is the same thing.
 */
export function Ltr({ children }: { children: ReactNode }) {
  if (locale.dir !== 'rtl') return <>{children}</>
  return <bdi dir="ltr">{children}</bdi>
}

