import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { RING } from '@/pages/shop/shared'
import { applyUpdate, onSwState, startSw } from '../lib/sw'

/** "New version ready · Refresh" — shown above the bottom nav, never auto-reloading mid-cart. */
export function UpdateToast() {
  const [need, setNeed] = useState(false)
  useEffect(() => {
    if (!import.meta.env.PROD) return
    startSw()
    return onSwState(setNeed)
  }, [])
  if (!need) return null
  return (
    <div className="fixed inset-x-3 z-[60] md:inset-x-auto md:right-5" style={{ bottom: 'calc(4.25rem + env(safe-area-inset-bottom, 0px) + 0.75rem)' }} role="status">
      <div className="flex items-center gap-3 rounded-2xl border border-[#e9e2f8] bg-[#1a1430] px-4 py-3 text-white shadow-[0_18px_40px_-18px_rgba(26,20,48,.6)]">
        <span className="flex-1 text-[13px] font-medium">A new version of YQ Marketplace is ready.</span>
        <button type="button" onClick={() => void applyUpdate()} className={cn('inline-flex h-9 items-center gap-1.5 rounded-xl bg-white px-3 text-[12.5px] font-semibold text-[#1a1430]', RING)}>
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </button>
      </div>
    </div>
  )
}
