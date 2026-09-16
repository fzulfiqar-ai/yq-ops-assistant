import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Toasts on CSS keyframes, not an animation library. This provider is mounted by the
 * public shop, the marketplace AND the portal; the previous version pulled the `motion`
 * package (~200 KB uncompressed) into the merchant's first download for a 300 ms slide.
 * `prefers-reduced-motion` collapses the durations globally (index.css).
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

const MOTION = `
@keyframes yq-toast-in { from { opacity: 0; transform: translate3d(0, 16px, 0) scale(.97) } to { opacity: 1; transform: translate3d(0, 0, 0) scale(1) } }
@keyframes yq-toast-out { from { opacity: 1; transform: translate3d(0, 0, 0) } to { opacity: 0; transform: translate3d(32px, 0, 0) } }
.yq-toast { animation: yq-toast-in 260ms cubic-bezier(.16, 1, .3, 1) both }
.yq-toast--leaving { animation: yq-toast-out 180ms ease-in both }
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

  return (
    <ToastCtx.Provider value={{ toast }}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-3 bottom-[calc(env(safe-area-inset-bottom,0px)+5.25rem)] z-[100] flex flex-col items-end gap-2 sm:inset-x-auto sm:bottom-5 sm:right-5 sm:w-full sm:max-w-sm"
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
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="text-muted-foreground transition hover:text-foreground"
            >
              <X size={15} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const c = useContext(ToastCtx)
  if (!c) throw new Error('useToast must be used within ToastProvider')
  return c.toast
}
