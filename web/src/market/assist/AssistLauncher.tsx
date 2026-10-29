import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation } from 'react-router-dom'
import { MessageCircle, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { enabledDevForced, useAssistEnabled } from './enabled'
import {
  fetchNudge,
  noteSeen,
  OPEN_EVENT,
  peekAllowed,
  peekDismissed,
  peekSpent,
  READY_EVENT,
  rememberOpen,
  wasOpen,
  type FirstMessage,
  type Nudge,
  type OpenOptions,
} from './open'
import { L } from './launcherStrings'
import { Orb } from './Orb'

// 2.1 (owner, 29-Oct-2026: "proactive", on from release): the line beside the button, once per visit, after a
// while on a page that sells; never on the cart, checkout, orders or the chat's own page. 2.2: the line is the
// server's (a clearing line, his campaign, the shop's usual, what sells: a new one each visit), else the static one.
const PEEK_AFTER_MS = 15000
const PEEK_FOR_MS = 12000

function peekPage(pathname: string): 'item' | 'shop' | null {
  if (pathname.startsWith('/p/')) return 'item'
  if (pathname === '/' || pathname === '/shop' || pathname === '/search' || pathname.startsWith('/t/') || pathname.startsWith('/brands/')) return 'shop'
  return null
}

const loadPanel = () => import('./AssistPanel')
const AssistPanel = lazy(loadPanel)

/** True while the phone's bottom stack (cart dock + nav) has slid away on scroll-down: the button follows it down. */
function useStackHidden(): boolean {
  const [hidden, setHidden] = useState(false)
  useEffect(() => {
    let timer = 0
    const read = () => {
      const stack = document.querySelector<HTMLElement>('.fixed.bottom-0.z-nav')
      const tf = stack ? getComputedStyle(stack).transform : 'none'
      const ty = tf && tf !== 'none' ? Number(tf.replace(/^matrix\((.*)\)$/, '$1').split(',')[5]) : 0
      setHidden(Number.isFinite(ty) && ty > 8)
    }
    const onScroll = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(read, 90)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.clearTimeout(timer)
    }
  }, [])
  return hidden
}

/**
 * The floating "Ask YQ" button, mounted once in the Shell (ShellBody, beside the page slot, so a page
 * change never remounts it). Renders nothing unless the catalog
 * says the assistant is on, and nothing on checkout, /ask and the tracking page. Phone: a round
 * 52px plum button at the bottom-end, riding above the bottom stack. md and up: a pill. The panel
 * is lazy, so the first download carries only this button.
 */
export function AssistLauncher() {
  const enabled = useAssistEnabled()
  const { settings } = useMarket()
  const nudgeOn = settings.ai_nudge === true || enabledDevForced()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(wasOpen)
  const [itemCode, setItemCode] = useState<string | null>(null)
  const [first, setFirst] = useState<FirstMessage | null>(null)
  const stackHidden = useStackHidden()
  // v2: an answer that landed while the chat was closed (the chat keeps running), and the drop's rings on a tap
  const [ready, setReady] = useState(false)
  const [drop, setDrop] = useState(0)
  // 2.1: the line beside the button (the product's code when it shows on a product page); 2.2: the server's line
  const [peek, setPeek] = useState<{ kind: 'item' | 'shop'; code: string | null; nudge: Nudge | null } | null>(null)
  useEffect(() => {
    const on = () => setReady(true)
    window.addEventListener(READY_EVENT, on)
    return () => window.removeEventListener(READY_EVENT, on)
  }, [])
  if (open && ready) setReady(false)
  if (open && peek) setPeek(null)

  useEffect(() => {
    if (!enabled) return
    const on = (e: Event) => {
      const d = (e as CustomEvent<OpenOptions>).detail
      setItemCode(d?.itemCode || null)
      setFirst(d?.message ? { id: Date.now(), text: d.message } : null)
      setOpen(true)
    }
    window.addEventListener(OPEN_EVENT, on)
    return () => window.removeEventListener(OPEN_EVENT, on)
  }, [enabled])

  useEffect(() => {
    rememberOpen(open)
    if (open) peekSpent() // the chat was opened this visit: the line has done its job
  }, [open])

  // 2.1: after a while on a page that sells, once per visit (a dismiss rests it for a day)
  useEffect(() => {
    const kind = peekPage(pathname)
    if (!enabled || !nudgeOn || open || !kind || !peekAllowed()) return
    let live = true
    const t = window.setTimeout(() => {
      if (!peekAllowed()) return
      // a sheet over a shop page (filters, the cart) hides the button: wait for the next page rather than speak unseen
      if (kind === 'shop' && document.querySelector('[role="dialog"][aria-modal="true"]')) return
      peekSpent()
      let code: string | null
      try {
        code = kind === 'item' ? decodeURIComponent(pathname.split('/')[2] || '') || null : null
      } catch {
        code = null
      }
      // 2.2: the server's line (2 s at most), else the static one; left the page or opened the chat meanwhile: none
      void fetchNudge(code).then((nudge) => {
        if (!live) return
        if (nudge) noteSeen(nudge.hook)
        setPeek({ kind, code, nudge })
      })
    }, PEEK_AFTER_MS)
    return () => {
      live = false
      window.clearTimeout(t)
    }
  }, [pathname, enabled, nudgeOn, open])
  useEffect(() => {
    if (!peek) return
    const t = window.setTimeout(() => setPeek(null), PEEK_FOR_MS)
    return () => window.clearTimeout(t)
  }, [peek])

  // a phone chat is a full screen: following a link out of it (cart, an order) leaves it.
  // Every close also forgets the opening question, so reopening later never sends it again.
  const [pathSeen, setPathSeen] = useState(pathname)
  if (pathSeen !== pathname) {
    setPathSeen(pathname)
    setPeek(null)
    if (open && typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches) {
      setOpen(false)
      setFirst(null)
    }
  }

  // a tap on the line is a yes: the chat opens on the answer it offered (from data, no model)
  const takePeek = () => {
    if (!peek) return
    setItemCode(peek.code)
    const n = peek.nudge
    setFirst(
      n
        ? { id: Date.now(), text: n.label, action: n.action, q: n.q, nudge: n.hook }
        : peek.kind === 'item' && peek.code
          ? { id: Date.now(), text: L.firstItem, action: 'fit' }
          : { id: Date.now(), text: L.firstShop, action: 'top' },
    )
    setPeek(null)
    setDrop((n) => n + 1)
    setOpen(true)
  }

  const close = useCallback(() => {
    setOpen(false)
    setFirst(null)
  }, [])

  const hiddenHere = pathname === '/checkout' || pathname === '/ask' || pathname.startsWith('/o/')
  if (!enabled || hiddenHere) return null
  const bottom = stackHidden ? 'var(--m-safe-b, 0px)' : 'var(--m-bottom-stack, 0px)'

  return createPortal(
    <>
      {open ? (
        <Suspense fallback={null}>
          <AssistPanel itemCode={itemCode} first={first} onClose={close} />
        </Suspense>
      ) : null}
      {peek && !open ? (
        <div
          role="status"
          data-assist-peek=""
          style={{ bottom: `calc(${bottom} + 76px)` }}
          className={cn(
            'assist-in fixed end-3 flex max-w-[min(18rem,calc(100vw-1.5rem))] items-center gap-0.5 rounded-2xl rounded-ee-md bg-surface p-1 shadow-3 ring-1 ring-line md:end-5',
            // a product opens as a sheet over the shop (z-sheet 50, which covers the button): the line rides above it
            peek.kind === 'item' ? 'z-[55]' : 'z-[44]',
          )}
        >
          <button
            type="button"
            onClick={takePeek}
            onPointerEnter={() => void loadPanel()}
            className="min-h-12 flex-1 rounded-xl px-3 py-2 text-start text-sm font-semibold leading-snug text-ink hover:text-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
          >
            {peek.nudge?.text || (peek.kind === 'item' && peek.code ? L.peekItem : L.peekShop)}
          </button>
          <button
            type="button"
            aria-label={L.peekClose}
            title={L.peekClose}
            onClick={() => {
              peekDismissed()
              setPeek(null)
            }}
            className="grid h-12 w-12 shrink-0 place-items-center rounded-full text-ink-3 hover:bg-plum-wash hover:text-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      ) : null}
      <button
        type="button"
        aria-label={ready ? L.ready : L.label}
        aria-expanded={open}
        aria-haspopup="dialog"
        data-assist-launcher=""
        onClick={() => {
          setDrop((n) => n + 1)
          if (open) close()
          else setOpen(true)
        }}
        onPointerEnter={() => void loadPanel()}
        onFocus={() => void loadPanel()}
        style={{ bottom: stackHidden ? 'calc(var(--m-safe-b, 0px) + 12px)' : 'calc(var(--m-bottom-stack, 0px) + 12px)' }}
        className={cn(
          'fixed end-3 z-[44] inline-flex h-[54px] w-[54px] items-center justify-center gap-2.5 rounded-full font-semibold transition-[bottom,transform] duration-3 ease-m active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas',
          'md:bottom-5 md:end-5 md:h-12 md:w-auto md:bg-surface md:pe-5 md:ps-1.5 md:text-sm md:text-plum-ink md:shadow-3 md:ring-1 md:ring-line md:hover:ring-plum/40',
          open && 'max-md:hidden',
        )}
      >
        <span className="relative grid place-items-center">
          {/* the phone button carries the chat icon; the desktop pill's orb (and every orb in the chat) the YQ mark */}
          <Orb size={54} mark={false} className="shadow-3 md:hidden" />
          <Orb size={38} className="max-md:hidden" />
          <MessageCircle size={20} aria-hidden="true" className="absolute text-white md:hidden" />
          {drop > 0 ? (
            <span key={drop} className="assist-rings go" aria-hidden="true">
              <span />
              <span />
            </span>
          ) : null}
          {ready ? <span aria-hidden="true" className="absolute -end-0.5 -top-0.5 h-3.5 w-3.5 rounded-full bg-bad ring-2 ring-surface" /> : null}
        </span>
        <span className="hidden md:inline">{L.text}</span>
      </button>
    </>,
    document.body,
  )
}
