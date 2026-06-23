import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

type ToastKind = 'success' | 'error' | 'info'
interface ToastItem { id: number; kind: ToastKind; message: string }
interface ToastCtxValue { toast: (message: string, kind?: ToastKind) => void }

const ToastCtx = createContext<ToastCtxValue | null>(null)

const STYLE: Record<ToastKind, { ring: string; icon: ReactNode }> = {
  success: { ring: 'border-emerald-300/70 dark:border-emerald-500/30', icon: <CheckCircle2 size={18} className="text-emerald-500" /> },
  error: { ring: 'border-rose-300/70 dark:border-rose-500/30', icon: <AlertCircle size={18} className="text-rose-500" /> },
  info: { ring: 'border-primary/30', icon: <Info size={18} className="text-primary" /> },
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const dismiss = (id: number) => setItems((t) => t.filter((x) => x.id !== id))
  const toast = useCallback((message: string, kind: ToastKind = 'success') => {
    const id = Date.now() + Math.random()
    setItems((t) => [...t, { id, kind, message }])
    setTimeout(() => dismiss(id), 4200)
  }, [])

  return (
    <ToastCtx.Provider value={{ toast }}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-full max-w-sm flex-col gap-2">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 20, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, transition: { duration: 0.2 } }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
              className={cn('pointer-events-auto flex items-start gap-3 rounded-xl border bg-card p-3.5 shadow-lift', STYLE[t.kind].ring)}
            >
              {STYLE[t.kind].icon}
              <p className="flex-1 text-sm font-medium leading-snug text-foreground">{t.message}</p>
              <button onClick={() => dismiss(t.id)} className="text-muted-foreground transition hover:text-foreground">
                <X size={15} />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
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
