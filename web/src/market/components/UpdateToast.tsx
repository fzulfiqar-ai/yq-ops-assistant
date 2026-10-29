import { useEffect, useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import { applyUpdate, onSwState, startSw } from '../lib/sw'
import { S } from '../strings'

/** "New version ready · Refresh" — above the bottom stack, never auto-reloading mid-cart. */
export function UpdateToast() {
  const [need, setNeed] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  useEffect(() => {
    if (!import.meta.env.PROD) return
    startSw()
    return onSwState(setNeed)
  }, [])
  if (!need || dismissed) return null
  // At the top, under the sheets' layer (z 45: above the header, dock and nav, below a sheet), and
  // dismissable: it sat in the toasts' own spot and layer, so the two overlapped, it covered an open
  // sheet's footer, and it could not be put away (28-Oct-2026).
  return (
    <div className="fixed inset-x-3 z-[45] mx-auto max-w-md anim-fade-in" style={{ top: 'calc(var(--m-safe-t) + 10px)' }} role="status">
      <div className="flex items-center gap-2 rounded-md bg-ink py-2 pe-2 ps-4 text-white shadow-3">
        <span className="flex-1 text-sm font-medium">{S.states.updateReady}</span>
        <button type="button" onClick={() => void applyUpdate()} className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-white px-3 text-sm font-semibold text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70">
          <RefreshCw size={14} aria-hidden="true" /> {S.states.refresh}
        </button>
        <button type="button" onClick={() => setDismissed(true)} aria-label={S.states.dismiss} className="grid h-9 w-9 place-items-center rounded-sm text-white/70 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70">
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
