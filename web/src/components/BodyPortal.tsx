import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * Renders a page's hand-rolled overlay (a `fixed inset-0 z-50` backdrop) outside the shell's <main>.
 *
 * Why (R7e, owner screenshot): AppShell's `<main className="relative z-10">` is a stacking context BELOW
 * the sticky z-20 top bar, so an overlay left inside it opened under the bar whatever its own z-index:
 * the Customer orders drawer's order number, status pills and Close were hidden.
 *
 * Where it goes: beside that <main> (its parent), not straight into document.body. The whole app sits in
 * ArcRevealHero's isolated `relative z-0` layer, toasts (z-[100]) included; an overlay at the body level
 * would stack above that entire layer and hide every toast an open drawer or dialog raises. Beside <main>
 * it stacks against the top bar (z-20) and under the toasts, and inside the salesman app it stays in the
 * shell root that carries the plum tokens (SalesmanShell, data-shell="sales"). A page with no shell
 * <main> falls back to document.body. The theme (data-theme on <html>), React context (query, auth,
 * toast) and React events (they bubble through the React tree, not the DOM) are unchanged.
 *
 * Named apart from PortalRoot.tsx (the office portal's app root), which has nothing to do with this.
 */
export function BodyPortal({ children }: { children: ReactNode }) {
  // the first <main> in document order is the shell's own (a page's nested <main> sits inside it)
  const host = document.querySelector('main')?.parentElement ?? document.body
  return createPortal(children, host)
}
