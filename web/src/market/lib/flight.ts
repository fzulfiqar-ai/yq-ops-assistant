/**
 * Photo flight (28-Oct-2026 polish, motion list M4): the tapped card's photo flies into the product
 * sheet and lands on the sheet's own photo, while the sheet slides in under it.
 *
 * FLIP on a clone with the Web Animations API — transform only, every browser, no router feature.
 * (The View Transition morph this replaces never ran: the market's BrowserRouter ignores
 * `viewTransition`.) The card's photo is already decoded, so the clone paints at once; the sheet's
 * photo stays hidden until the clone lands on it, then takes over (its own fade-in covers a photo
 * still loading). Skipped under reduced motion, on a weak phone (html[data-lite]), and when the
 * card's photo is not on screen or not loaded — the sheet then simply opens as before.
 */

const FLIGHT_MS = 300
const EASE = 'cubic-bezier(0.32, 0.72, 0, 1)' // the sheet's own curve: they land together

interface Flight {
  code: string
  box: Box
  src: string
  at: number
}

interface Box {
  left: number
  top: number
  width: number
  height: number
}

let pending: Flight | null = null

/** the image's content box (its rect without padding): where object-contain draws the photo */
function contentBox(img: HTMLImageElement, offsetX = 0, offsetY = 0): Box {
  const r = img.getBoundingClientRect()
  const cs = getComputedStyle(img)
  const pl = parseFloat(cs.paddingLeft) || 0
  const pr = parseFloat(cs.paddingRight) || 0
  const pt = parseFloat(cs.paddingTop) || 0
  const pb = parseFloat(cs.paddingBottom) || 0
  return { left: r.left + pl - offsetX, top: r.top + pt - offsetY, width: r.width - pl - pr, height: r.height - pt - pb }
}

function calm(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches || 'lite' in document.documentElement.dataset
  } catch {
    return true
  }
}

/** Card side, at the tap: remember where the photo is. */
export function launchFlight(code: string, img: HTMLImageElement | null | undefined): void {
  pending = null
  if (!img || !img.complete || !img.naturalWidth || calm()) return
  const box = contentBox(img)
  if (box.width < 40 || box.top + box.height < 0 || box.top > window.innerHeight) return
  pending = { code, box, src: img.currentSrc || img.src, at: performance.now() }
}

/** Sheet side, as it mounts (a layout effect): fly the clone onto `hero`'s photo. */
export function landFlight(code: string, hero: HTMLElement | null): void {
  const f = pending
  pending = null
  if (!f || f.code !== code || !hero || performance.now() - f.at > 1500) return
  const target = hero.querySelector<HTMLImageElement>('img')
  if (!target) return
  // where the photo will REST: its rect now, minus the sheet's entrance offset (it starts
  // off-screen and slides in on the same curve and about the same time)
  const sheet = hero.closest<HTMLElement>('[data-sheet-panel]')
  let ox = 0
  let oy = 0
  if (sheet) {
    const t = getComputedStyle(sheet).transform
    if (t && t !== 'none') {
      const m = new DOMMatrixReadOnly(t)
      ox = m.m41
      oy = m.m42
    }
  }
  const to = contentBox(target, ox, oy)
  if (to.width < 40) return
  const scale = to.width / f.box.width
  const dx = to.left - f.box.left
  const dy = to.top - f.box.top

  const clone = new Image()
  clone.src = f.src
  clone.alt = ''
  clone.setAttribute('aria-hidden', 'true')
  Object.assign(clone.style, {
    position: 'fixed',
    left: `${f.box.left}px`,
    top: `${f.box.top}px`,
    width: `${f.box.width}px`,
    height: `${f.box.height}px`,
    objectFit: 'contain',
    transformOrigin: '0 0',
    zIndex: '75',
    pointerEvents: 'none',
  })
  document.body.appendChild(clone)
  // hidden at once (its own 200 ms fade would show two photos for the first part of the flight)
  target.style.transition = 'none'
  target.style.opacity = '0'

  let done = false
  const finish = () => {
    if (done) return
    done = true
    // hand over once the sheet's own photo can paint, at most 600 ms on: it is a larger file than the
    // card's and may still be loading as the clone lands, so the clone holds its place instead of an
    // empty tile (round 2). Then the photo's own fade brings it in while the clone fades out over it.
    let handed = false
    const handOver = () => {
      if (handed) return
      handed = true
      target.style.transition = ''
      target.style.opacity = ''
      clone.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: 'linear' }).onfinish = () => clone.remove()
    }
    if (target.complete && target.naturalWidth) handOver()
    else {
      target.decode().then(handOver, handOver)
      window.setTimeout(handOver, 600)
    }
  }
  const flight = clone.animate([{ transform: 'translate3d(0, 0, 0) scale(1)' }, { transform: `translate3d(${dx}px, ${dy}px, 0) scale(${scale})` }], {
    duration: FLIGHT_MS,
    easing: EASE,
    fill: 'forwards',
  })
  flight.onfinish = finish
  // a sheet closed mid-flight, a tab hidden: never leave the clone or a hidden photo behind
  window.setTimeout(finish, FLIGHT_MS + 400)
}

/**
 * Add-to-restock flight (29-Oct-2026, motion K4): "Add" sends a small copy of the card's photo in an
 * arc to where the cart lives, and the cart gives a small pulse as it lands. The target is the
 * lowest-numbered [data-cart-target] on screen — 1 the mini-cart column (a wide PC), 2 the header's
 * Restock button, 3 the bottom bar's Restock tab (a phone) — and with none showing (the phone's bar
 * hides while scrolling down) the middle of the screen's bottom edge, where that bar lives. Same rules
 * as the photo flight: transform and opacity only, skipped when calm() or with the photo off screen.
 */
const CART_FLIGHT_MS = 620
const CART_EASE = 'cubic-bezier(0.45, 0, 0.2, 1)'
const LAND_PX = 28

function cartTarget(): { x: number; y: number; el: HTMLElement | null } {
  const shown = Array.from(document.querySelectorAll<HTMLElement>('[data-cart-target]'))
    .filter((el) => {
      const r = el.getBoundingClientRect()
      return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight
    })
    .sort((a, b) => Number(a.dataset.cartTarget) - Number(b.dataset.cartTarget))
  const el = shown[0]
  if (!el) return { x: window.innerWidth / 2, y: window.innerHeight - LAND_PX, el: null }
  const r = el.getBoundingClientRect()
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, el }
}

/** a sheet, the palette or the photo viewer on screen (one leaving counts until it has gone) */
const layerOnScreen = () => Array.from(document.querySelectorAll('[role="dialog"]')).some((d) => d.getBoundingClientRect().height > 0)
const LAYER_WAIT_MS = 700

/**
 * An add made inside a layer (the keypad's Apply, a row of the search palette) waits for the layer
 * to leave, then flies from where the card's photo is: the tile never crosses a dim towards a cart the
 * dim hides (final review, 29-Oct-2026). A layer that stays open (the palette) means no flight.
 */
export function flyToCart(img: HTMLImageElement | null | undefined): void {
  if (!img || calm()) return
  const since = performance.now()
  const go = () => {
    if (!layerOnScreen()) launchToCart(img)
    else if (performance.now() - since < LAYER_WAIT_MS) requestAnimationFrame(go)
  }
  go()
}

function launchToCart(img: HTMLImageElement): void {
  if (!img.isConnected || !img.complete || !img.naturalWidth) return
  const box = contentBox(img)
  if (box.width < 24 || box.top + box.height < 0 || box.top > window.innerHeight) return
  const target = cartTarget()
  if (!target.el) {
    // a phone's bar hides while scrolling down: call it back (MobileShell) and land on its Restock tab
    // where it will rest - its transform is the bar's slide still to come
    const tab = document.querySelector<HTMLElement>('[data-cart-target="3"]')
    if (tab) {
      window.dispatchEvent(new Event('yq:cart-flight'))
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          const r = tab.getBoundingClientRect()
          const stack = tab.closest<HTMLElement>('[data-bottom-stack]')
          const t = stack ? getComputedStyle(stack).transform : 'none'
          const dy = t && t !== 'none' ? new DOMMatrixReadOnly(t).m42 : 0
          flyTile(img, box, { x: r.left + r.width / 2, y: r.top + r.height / 2 - dy, el: tab })
        }),
      )
      return
    }
  }
  // a cart something else covers (a toast, a dim) is no place to land: no flight
  if (target.el) {
    const hit = document.elementFromPoint(target.x, target.y)
    if (hit && !target.el.contains(hit) && !hit.contains(target.el)) return
  }
  flyTile(img, box, target)
}

function flyTile(img: HTMLImageElement, box: Box, target: { x: number; y: number; el: HTMLElement | null }): void {
  // a square tile of the photo, centred on it, at most 140 px: that is what flies
  const side = Math.min(box.width, box.height, 140)
  const x0 = box.left + box.width / 2
  const y0 = box.top + box.height / 2
  const dx = target.x - x0
  const dy = target.y - y0
  // a quadratic curve through a control point lifted above the middle of the straight line, sampled
  // into keyframes: the tile rises, then falls into the cart, shrinking all the way
  const lift = -Math.min(160, Math.hypot(dx, dy) * 0.35)
  const end = LAND_PX / side
  const steps = 10
  const frames: Keyframe[] = []
  for (let i = 0; i <= steps; i += 1) {
    const u = i / steps
    const x = 2 * (1 - u) * u * (dx / 2) + u * u * dx
    const y = 2 * (1 - u) * u * (dy / 2 + lift) + u * u * dy
    frames.push({ transform: `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) scale(${(1 + (end - 1) * Math.pow(u, 0.8)).toFixed(3)})`, opacity: u < 0.85 ? 1 : 1 - (u - 0.85) * 2 })
  }

  const tile = new Image()
  tile.src = img.currentSrc || img.src
  tile.alt = ''
  tile.setAttribute('aria-hidden', 'true')
  tile.dataset.cartFlight = ''
  Object.assign(tile.style, {
    position: 'fixed',
    left: `${x0 - side / 2}px`,
    top: `${y0 - side / 2}px`,
    width: `${side}px`,
    height: `${side}px`,
    objectFit: 'contain',
    padding: '8px',
    background: '#fff',
    borderRadius: '16px',
    boxShadow: '0 12px 32px -10px rgba(42, 18, 89, 0.35)',
    zIndex: '75',
    pointerEvents: 'none',
  })
  document.body.appendChild(tile)

  let done = false
  const finish = () => {
    if (done) return
    done = true
    tile.remove()
    // the individual `scale` property composes with the target's own transform (the phone's active
    // Restock tab sits at scale 1.08): a `transform` keyframe replaced it, and the icon snapped
    target.el?.animate([{ scale: '1' }, { scale: '1.14' }, { scale: '1' }], { duration: 300, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' })
  }
  tile.animate(frames, { duration: CART_FLIGHT_MS, easing: CART_EASE, fill: 'forwards' }).onfinish = finish
  window.setTimeout(finish, CART_FLIGHT_MS + 400)
}
