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
    // hand over: the photo's own fade brings it in while the clone fades out over it
    target.style.transition = ''
    target.style.opacity = ''
    clone.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: 'linear' }).onfinish = () => clone.remove()
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
