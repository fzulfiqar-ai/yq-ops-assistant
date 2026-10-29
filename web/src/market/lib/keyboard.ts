/**
 * iOS opens the on-screen keyboard only for a focus() made inside the tap itself. Search opens on
 * its own page, whose field mounts after the tap (SearchPage's autoFocus), so on an iPhone the
 * field took the focus with no keyboard and the merchant had to tap again (28-Oct-2026).
 *
 * The tap focuses this off-screen proxy first — the keyboard comes up — and the page's field takes
 * the focus over when it mounts (moving focus between fields keeps the keyboard). 16 px so iOS does
 * not zoom; gone after a moment either way. Harmless elsewhere: other browsers simply move the focus.
 */
export function primeKeyboard(): void {
  try {
    const proxy = document.createElement('input')
    proxy.setAttribute('aria-hidden', 'true')
    proxy.tabIndex = -1
    Object.assign(proxy.style, { position: 'fixed', top: '0', left: '0', width: '1px', height: '1px', opacity: '0', fontSize: '16px', border: '0', padding: '0', pointerEvents: 'none' })
    document.body.appendChild(proxy)
    proxy.focus({ preventScroll: true })
    window.setTimeout(() => proxy.remove(), 1500)
  } catch {
    /* a proxy that cannot be made only costs the merchant one more tap */
  }
}
