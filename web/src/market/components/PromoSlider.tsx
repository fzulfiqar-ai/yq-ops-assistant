import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { ChevronLeft, ChevronRight, Pause, Play } from 'lucide-react'
import { cn } from '@/lib/utils'
import { SLIDE_DWELL, useCarousel, useClaimSlides, type Slide } from '../lib/slides'
import { useReducedMotion } from '../shell/useViewport'
import { S } from '../strings'
import { SlideCard } from './SlideCard'

/**
 * The promo slider (plan D7): campaigns + data slides from lib/slides.ts, one list, never a repeat.
 *
 * Phone — a scroll-snap track (`.slider-track`) that bleeds to the screen edges (put it in a
 * `px-gutter` parent): cards at 88% width peek the next one, 2:1, dots under it. Autoplay every
 * 5.5 s by smooth-scrolling the track; the index follows the scroll position. Pauses while the
 * merchant touches or scrolls the track (and for 8 s after), while it has keyboard focus, when the
 * tab is hidden or the slider is off-screen; never plays under reduced motion. A 44 px pause/play
 * button sits at the end of the dots row — the only way to stop it for good (WCAG 2.2.2).
 *
 * Desktop — a 21:9 stage that crossfades (the incoming slide fades in over the outgoing one, which
 * stays opaque, so the canvas never dips), inactive slides inert + aria-hidden; under it a control
 * row: dots that fill over the dwell time (the fill pauses with the timer), pause/play, prev/next.
 * Arrow keys move too, but only from that row — inside the stage they would turn the focused slide
 * inert under the merchant. Hover pauses.
 *
 * Both register their slide ids (useClaimSlides) so the aside Spotlight never repeats them.
 */

export interface PromoSliderProps {
  slides: Slide[]
  layout: 'phone' | 'desktop'
  className?: string
}

export function PromoSlider({ slides, layout, className }: PromoSliderProps) {
  useClaimSlides(slides.map((s) => s.id))
  if (!slides.length) return null
  return layout === 'phone' ? <PhoneSlider slides={slides} className={className} /> : <DesktopSlider slides={slides} className={className} />
}

/* ───────────────────────── phone: snap track ───────────────────────── */

function PhoneSlider({ slides, className }: { slides: Slide[]; className?: string }) {
  const n = slides.length
  const reduced = useReducedMotion()
  const rootRef = useRef<HTMLElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  // a scroll we started (autoplay) must not count as the merchant scrolling
  const programmatic = useRef(false)
  const settle = useRef(0)
  const frame = useRef(0)

  const scrollToIndex = useCallback((i: number) => {
    const track = trackRef.current
    const cell = track?.children[i] as HTMLElement | undefined
    if (!track || !cell) return
    const max = track.scrollWidth - track.clientWidth
    const left = Math.max(0, Math.min(max, cell.offsetLeft - (track.clientWidth - cell.offsetWidth) / 2))
    if (Math.abs(track.scrollLeft - left) < 2) return
    programmatic.current = true
    window.clearTimeout(settle.current)
    // no scroll event at all (already there, or smooth scroll unsupported): release anyway
    settle.current = window.setTimeout(() => (programmatic.current = false), 1000)
    track.scrollTo({ left, behavior: reduced ? 'auto' : 'smooth' })
  }, [reduced])

  const c = useCarousel({ count: n, reduced, rootRef, onAdvance: scrollToIndex })
  const { sync, hold } = c

  const nearest = useCallback(() => {
    const track = trackRef.current
    if (!track) return
    const centre = track.scrollLeft + track.clientWidth / 2
    let best = 0
    let dist = Infinity
    for (let i = 0; i < track.children.length; i++) {
      const cell = track.children[i] as HTMLElement
      const d = Math.abs(cell.offsetLeft + cell.offsetWidth / 2 - centre)
      if (d < dist) {
        dist = d
        best = i
      }
    }
    sync(best)
  }, [sync])

  const onScroll = () => {
    if (!programmatic.current) hold()
    window.clearTimeout(settle.current)
    settle.current = window.setTimeout(() => {
      programmatic.current = false
      nearest()
    }, 140)
    if (programmatic.current || frame.current) return
    frame.current = window.requestAnimationFrame(() => {
      frame.current = 0
      nearest()
    })
  }
  useEffect(
    () => () => {
      window.clearTimeout(settle.current)
      window.cancelAnimationFrame(frame.current)
    },
    [],
  )

  return (
    <section ref={rootRef} aria-roledescription="carousel" aria-label={S.slides.label} className={cn('-mx-gutter', className)} {...c.bind} onTouchStart={hold}>
      <div ref={trackRef} onScroll={onScroll} className="slider-track relative gap-2.5 px-gutter">
        {slides.map((s, i) => (
          <div key={s.id} role="group" aria-roledescription="slide" aria-label={S.slides.go(i + 1, n)} data-slide-state={i === c.index ? 'active' : 'idle'} className={cn(n > 1 ? 'w-[88%] md:w-[60%]' : 'w-full md:w-[60%]', 'max-w-[560px]')}>
            <SlideCard slide={s} size="phone" priority={i === 0} where="slider" />
          </div>
        ))}
      </div>
      {n > 1 && (
        <>
          <div className={cn('relative flex items-center justify-center gap-1.5', c.autoplay ? 'mt-1 h-11' : 'mt-2.5 h-1.5')}>
            <span className="flex items-center gap-1.5" aria-hidden="true">
              {slides.map((s, i) => (
                <span key={s.id} className={cn('h-1.5 rounded-full transition-[width,background-color] duration-3 ease-m', i === c.index ? 'w-[18px] bg-plum' : 'w-1.5 bg-ink/20')} />
              ))}
            </span>
            {/* WCAG 2.2.2: touching the track only holds the slides for SLIDE_RESUME, so the merchant
                (and anyone reading with a browse cursor, which never fires focus) needs a real stop.
                Absolute, so it never pushes the dots off centre; gone when nothing moves on its own. */}
            {c.autoplay && (
              <button
                type="button"
                onClick={c.togglePause}
                aria-label={c.userPaused ? S.slides.play : S.slides.pause}
                className="absolute end-gutter top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full text-ink-3 transition duration-1 ease-m hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
              >
                {c.userPaused ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
              </button>
            )}
          </div>
          {/* the dots are decorative on phones; this is their text. Silent while autoplay turns the
              slides (a status every 5.5 s would talk over the page), polite once the merchant swipes. */}
          <p className="sr-only" aria-live={c.running ? 'off' : 'polite'} aria-atomic="true">
            {S.slides.go(c.index + 1, n)}
          </p>
        </>
      )}
    </section>
  )
}

/* ───────────────────────── desktop: crossfade stage ───────────────────────── */

function DesktopSlider({ slides, className }: { slides: Slide[]; className?: string }) {
  const n = slides.length
  const reduced = useReducedMotion()
  const rootRef = useRef<HTMLElement>(null)
  const c = useCarousel({ count: n, reduced, rootRef })
  const idx = c.index

  // the slide that was showing: it stays opaque under the incoming one while that fades in
  const [shown, setShown] = useState({ cur: idx, prev: -1 })
  if (shown.cur !== idx) setShown({ cur: idx, prev: shown.cur })
  const prev = shown.cur === idx ? shown.prev : -1

  const go = (d: number) => c.go((idx + d + n) % n)
  const onKeyDown = (e: KeyboardEvent) => {
    if (n < 2 || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return
    // Not while focus is on a slide: turning the stage makes that slide inert and aria-hidden, so
    // the focused link leaves the focus order and the merchant is dropped on <body>. The control
    // row below the stage drives the carousel; inside it, arrows still move.
    if (e.target instanceof Element && e.target.closest('[data-slide-state]')) return
    e.preventDefault()
    const forward = (e.key === 'ArrowRight') !== (document.documentElement.dir === 'rtl')
    go(forward ? 1 : -1)
  }

  // 40 px under a mouse, the full 44 px on a touch screen (a tablet in landscape gets this shell too)
  const control =
    'grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line bg-surface text-ink transition duration-1 ease-m hover:border-ink/25 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11'

  return (
    <section ref={rootRef} aria-roledescription="carousel" aria-label={S.slides.label} className={className} {...c.bind} onKeyDown={onKeyDown}>
      <div className="slider-stage relative aspect-[21/9] overflow-hidden rounded-xl bg-surface-2" aria-live={c.running ? 'off' : 'polite'}>
        {slides.map((s, i) => {
          const active = i === idx
          return (
            <div
              key={s.id}
              role="group"
              aria-roledescription="slide"
              aria-label={S.slides.go(i + 1, n)}
              aria-hidden={active ? undefined : true}
              inert={!active}
              data-slide-state={active ? 'active' : 'idle'}
              className={cn('absolute inset-0', active ? 'z-[2] opacity-100 transition-opacity duration-[560ms] ease-m' : i === prev ? 'z-[1] opacity-100' : 'z-0 opacity-0')}
            >
              <SlideCard slide={s} size="hero" priority={i === 0} where="slider" className="h-full w-full" />
            </div>
          )
        })}
      </div>

      {n > 1 && (
        <div className="mt-3 flex items-center gap-3">
          <div className="flex min-w-0 items-center">
            {slides.map((s, i) => {
              const active = i === idx
              return (
                <button key={s.id} type="button" onClick={() => c.go(i)} aria-label={S.slides.go(i + 1, n)} aria-current={active ? 'true' : undefined} className="group/dot grid h-10 w-9 shrink-0 place-items-center rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 [@media(pointer:coarse)]:h-11">
                  <span className={cn('relative block h-1.5 overflow-hidden rounded-full transition-[width,background-color] duration-3 ease-m', active ? 'w-7 bg-ink/15' : 'w-1.5 bg-ink/25 group-hover/dot:bg-ink/50')}>
                    {active && (
                      <span
                        key={`${s.id}:${idx}`}
                        /* the fill follows the reading direction: it grows from the inline start */
                        className={cn('slide-dwell absolute inset-0 origin-left rounded-full bg-plum rtl:origin-right', !c.autoplay && 'is-static')}
                        style={{ animationDuration: `${SLIDE_DWELL}ms`, animationPlayState: c.running ? 'running' : 'paused' } as CSSProperties}
                      />
                    )}
                  </span>
                </button>
              )
            })}
          </div>
          <div className="ms-auto flex items-center gap-2">
            {c.autoplay && (
              <button type="button" onClick={c.togglePause} aria-label={c.userPaused ? S.slides.play : S.slides.pause} className={control}>
                {c.userPaused ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
              </button>
            )}
            <button type="button" onClick={() => go(-1)} aria-label={S.slides.prev} className={control}>
              <ChevronLeft size={18} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
            <button type="button" onClick={() => go(1)} aria-label={S.slides.next} className={control}>
              <ChevronRight size={18} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
