import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { applyUpdate, onSwState, startSw } from '../lib/sw'
import { S } from '../strings'

/** "New version ready · Refresh" — above the bottom stack, never auto-reloading mid-cart. */
export function UpdateToast() {
  const [need, setNeed] = useState(false)
  useEffect(() => {
    if (!import.meta.env.PROD) return
    startSw()
    return onSwState(setNeed)
  }, [])
  if (!need) return null
  return (
    <div className="fixed inset-x-3 z-toast md:inset-x-auto md:end-5" style={{ bottom: 'calc(var(--m-bottom-stack) + var(--m-safe-b) + 12px)' }} role="status">
      <div className="flex items-center gap-3 rounded-md bg-ink px-4 py-3 text-white shadow-3">
        <span className="flex-1 text-sm font-medium">{S.states.updateReady}</span>
        <button type="button" onClick={() => void applyUpdate()} className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-white px-3 text-sm font-semibold text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70">
          <RefreshCw size={14} aria-hidden="true" /> {S.states.refresh}
        </button>
      </div>
    </div>
  )
}
