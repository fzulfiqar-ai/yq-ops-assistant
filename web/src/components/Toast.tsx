import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Toasts on CSS keyframes, not an animation library. This provider is mounted by the
 * public shop and the portal (the marketplace has its own, market/ui/Toast); the previous
 * version pulled the `motion` package (~200 KB uncompressed) into the merchant's first
 * download for a 300 ms slide. `prefers-reduced-motion` collapses the durations globally (index.css).
 */

type ToastKind = 'success' | 'error' | 'info'
interface ToastItem { id: number; kind: ToastKind; message: string; leaving?: boolean }
interface ToastCtxValue { toast: (message: string, kind?: ToastKind) => void }

const ToastCtx = createContext<ToastCtxValue | null>(null)

const STYLE: Record<ToastKind, { ring: string; icon: ReactNode }> = {
  success: { ring: 'border-emerald-300/70 dark:border-emerald-500/30', icon: <CheckCircle2 size={18} className="text-emerald-500" /> },
  error: { ring: 'border-rose-300/70 dark:border-rose-500/30', icon: <AlertCircle size={18} className="text-rose-500" /> },
  info: { ring: 'border-primary/30', icon: <Info size={18} className="text-primary" /> },
}

/*
 * Where the stack sits (ship 2, 30-Oct-2026). On the bottom stack the shell and the pages publish, as
 * the catalog's Undo does (pages/shop/UndoToast): above the salesman tab bar (--yq-tabbar), the
 * catalog's order bar (--yq-dock-bottom) and the floating Spotted button (--yq-capture-room), and from
 * `sm` left of the docked order slip (--yq-dock-right). A toast used to sit 84 px up on every phone,
 * over the order bar's Review button. Those 84 px stay the floor on a phone: the merchant's public shop
 * pins its own order bar to the bottom edge without publishing it, and the office has nothing there.
 *
 * While a sheet is open (components/ui/sheet marks <html data-portal-sheet>) the stack steps aside from
 * the sheet's footer, where the next action is ("Delivered. Now tell the shop." used to cover Tell the
 * shop): the top of a phone, over the dim above the sheet; the bottom-left from `sm`, since drawers
 * sit right and dialogs centre. It is portalled to <body> at z-[100], above every sheet (z-50).
 */
const MOTION = `
@keyframes yq-toast-in { from { opacity: 0; transform: translate3d(0, 16px, 0) scale(.97) } to { opacity: 1; transform: translate3d(0, 0, 0) scale(1) } }
@keyframes yq-toast-out { from { opacity: 1; transform: translate3d(0, 0, 0) } to { opacity: 0; transform: translate3d(32px, 0, 0) } }
.yq-toast { animation: yq-toast-in 260ms cubic-bezier(.16, 1, .3, 1) both }
.yq-toast--leaving { animation: yq-toast-out 180ms ease-in both }
.yq-toasts {
  left: .75rem; right: .75rem;
  bottom: max(calc(5.25rem + env(safe-area-inset-bottom, 0px)),
    calc(var(--yq-tabbar, 0px) + env(safe-area-inset-bottom, 0px) + var(--yq-dock-bottom, 0px) + var(--yq-capture-room, 0px) + 8px));
}
@media (min-width: 640px) {
  .yq-toasts {
    left: auto; right: max(var(--yq-dock-right, 0px), 1.25rem); width: 100%; max-width: 24rem;
    bottom: max(1.25rem, calc(var(--yq-tabbar, 0px) + var(--yq-dock-bottom, 0px) + var(--yq-capture-room, 0px) + 8px));
  }
}
html[data-portal-sheet] .yq-toasts { bottom: auto; top: calc(env(safe-area-inset-top, 0px) + .5rem) }
@media (min-width: 640px) {
  html[data-portal-sheet] .yq-toasts { top: auto; bottom: 1.25rem; right: auto; left: 1.25rem }
}
`

const SHOW_MS = 4200
const LEAVE_MS = 180

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: number) => {
    setItems((t) => t.map((x) => (x.id === id ? { ...x, leaving: true } : x)))
    setTimeout(() => setItems((t) => t.filter((x) => x.id !== id)), LEAVE_MS)
  }, [])

  const toast = useCallback(
    (message: string, kind: ToastKind = 'success') => {
      const id = Date.now() + Math.random()
      setItems((t) => [...t, { id, kind, message }])
      setTimeout(() => dismiss(id), SHOW_MS)
    },
    [dismiss],
  )

  // The layer is portalled to <body> (28-Oct-2026): the portal's intro wraps the whole app in
  // `relative isolate`, so a toast rendered in place sat below every sheet (those portal to <body>),
  // and "Order confirmed", "Delivered" and cancel errors were invisible under an open sheet.
  return (
    <ToastCtx.Provider value={{ toast }}>
      {children}
      {createPortal(
      <div
        data-toasts=""
        className="yq-toasts pointer-events-none fixed z-[100] flex flex-col items-end gap-2"
        aria-live="polite"
      >
        <style>{MOTION}</style>
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cn(
              'yq-toast pointer-events-auto flex w-full items-start gap-3 rounded-xl border bg-card p-3.5 shadow-lift',
              t.leaving && 'yq-toast--leaving',
              STYLE[t.kind].ring,
            )}
          >
            {STYLE[t.kind].icon}
            <p className="flex-1 text-sm font-medium leading-snug text-foreground">{t.message}</p>
            {/* a 44 px target under a finger, without making the toast taller */}
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="-my-3 -me-2.5 grid h-11 w-11 shrink-0 place-items-center rounded-lg text-muted-foreground transition hover:text-foreground"
            >
              <X size={15} />
            </button>
          </div>
        ))}
      </div>,
        document.body,
      )}
    </ToastCtx.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const c = useContext(ToastCtx)
  if (!c) throw new Error('useToast must be used within ToastProvider')
  return c.toast
}
