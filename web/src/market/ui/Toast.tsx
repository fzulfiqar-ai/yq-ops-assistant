import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Market toasts: CSS keyframes, market tokens, an optional action ("Undo", "View order").
 * Sits above the bottom stack (nav + page bar) on phones, bottom-end on wider screens.
 * Same `useToast()` shape as the portal's Toast so call sites read the same.
 */

type ToastKind = 'success' | 'error' | 'info'
export interface ToastOptions {
  kind?: ToastKind
  action?: { label: string; onClick: () => void }
  /** ms visible; default 3200 (5000 with an action) */
  duration?: number
}
interface ToastItem extends ToastOptions {
  id: number
  message: string
  leaving?: boolean
}
type ToastFn = (message: string, kindOrOptions?: ToastKind | ToastOptions) => void

const ToastCtx = createContext<ToastFn | null>(null)

const ICON: Record<ToastKind, ReactNode> = {
  success: <CheckCircle2 size={18} className="text-ok" aria-hidden="true" />,
  error: <AlertCircle size={18} className="text-bad" aria-hidden="true" />,
  info: <Info size={18} className="text-plum" aria-hidden="true" />,
}

const LEAVE_MS = 180

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id)
    if (t) window.clearTimeout(t)
    timers.current.delete(id)
    setItems((list) => list.map((x) => (x.id === id ? { ...x, leaving: true } : x)))
    window.setTimeout(() => setItems((list) => list.filter((x) => x.id !== id)), LEAVE_MS)
  }, [])

  const toast = useCallback<ToastFn>(
    (message, kindOrOptions) => {
      const opts: ToastOptions = typeof kindOrOptions === 'string' ? { kind: kindOrOptions } : kindOrOptions || {}
      const id = Date.now() + Math.random()
      const duration = opts.duration ?? (opts.action ? 5000 : 3200)
      // one toast at a time on a phone: the newest replaces the rest
      setItems((list) => [...list.filter((x) => x.leaving).slice(-1), { id, message, ...opts }])
      timers.current.set(id, window.setTimeout(() => dismiss(id), duration))
    },
    [dismiss],
  )

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-3 z-toast flex flex-col items-center gap-2 md:inset-x-auto md:end-5 md:w-full md:max-w-sm md:items-end"
        style={{ bottom: 'calc(var(--m-bottom-stack) + var(--m-safe-b) + 12px)' }}
        aria-live="polite"
      >
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            style={{ animation: t.leaving ? `m-toast-out ${LEAVE_MS}ms ease-in both` : 'm-toast-in 260ms var(--m-ease-spring) both' }}
            className={cn('pointer-events-auto flex w-full max-w-sm items-center gap-3 rounded-md bg-ink px-3.5 py-3 text-white shadow-3')}
          >
            {ICON[t.kind || 'success']}
            <p className="min-w-0 flex-1 text-sm font-medium leading-snug">{t.message}</p>
            {t.action && (
              <button
                type="button"
                onClick={() => {
                  t.action?.onClick()
                  dismiss(t.id)
                }}
                className="shrink-0 rounded-xs px-2 py-1 text-sm font-semibold text-white underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
              >
                {t.action.label}
              </button>
            )}
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="-me-1 grid h-8 w-8 shrink-0 place-items-center rounded-xs text-white/70 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastFn {
  const c = useContext(ToastCtx)
  if (!c) throw new Error('useToast must be used within ToastProvider')
  return c
}
