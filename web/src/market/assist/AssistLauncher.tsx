import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation } from 'react-router-dom'
import { MessageCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAssistEnabled } from './enabled'
import { OPEN_EVENT, READY_EVENT, rememberOpen, wasOpen, type OpenOptions } from './open'
import { L } from './launcherStrings'
import { Orb } from './Orb'

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
  const { pathname } = useLocation()
  const [open, setOpen] = useState(wasOpen)
  const [itemCode, setItemCode] = useState<string | null>(null)
  const [first, setFirst] = useState<{ id: number; text: string } | null>(null)
  const stackHidden = useStackHidden()
  // v2: an answer that landed while the chat was closed (the chat keeps running), and the drop's rings on a tap
  const [ready, setReady] = useState(false)
  const [drop, setDrop] = useState(0)
  useEffect(() => {
    const on = () => setReady(true)
    window.addEventListener(READY_EVENT, on)
    return () => window.removeEventListener(READY_EVENT, on)
  }, [])
  if (open && ready) setReady(false)

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
  }, [open])

  // a phone chat is a full screen: following a link out of it (cart, an order) leaves it.
  // Every close also forgets the opening question, so reopening later never sends it again.
  const [pathSeen, setPathSeen] = useState(pathname)
  if (pathSeen !== pathname) {
    setPathSeen(pathname)
    if (open && typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches) {
      setOpen(false)
      setFirst(null)
    }
  }

  const close = useCallback(() => {
    setOpen(false)
    setFirst(null)
  }, [])

  const hiddenHere = pathname === '/checkout' || pathname === '/ask' || pathname.startsWith('/o/')
  if (!enabled || hiddenHere) return null

  return createPortal(
    <>
      {open ? (
        <Suspense fallback={null}>
          <AssistPanel itemCode={itemCode} first={first} onClose={close} />
        </Suspense>
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
          <Orb size={54} className="shadow-3 md:hidden" />
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
