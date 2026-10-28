import { useMemo, useState } from 'react'
import { Loader2, LogOut } from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { clearRepDevice, repOpenOrders } from './lib'

/**
 * Sign-out asks first — it was one tap, next to the account button — and then takes this rep's
 * work off a shared phone before the session ends (lib.clearRepDevice): every shop's cart, the shop
 * he was ordering for and the catalog's remembered search, so the next person who signs in on the
 * phone starts clean. When an order is still in progress the question says so, with its size.
 */
export function SignOutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { signOut } = useAuth()
  const [busy, setBusy] = useState(false)
  // read when the question opens: what is on this phone right now
  const pending = useMemo(() => (open ? repOpenOrders() : { orders: 0, lines: 0 }), [open])

  const confirm = async () => {
    setBusy(true)
    clearRepDevice()
    try {
      await signOut()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={busy ? () => {} : onClose}
      variant="dialog"
      title="Sign out?"
      footer={
        <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="h-12 rounded-xl border border-[#E2DCEA] bg-white text-[14px] font-semibold text-[#1A1428] hover:bg-[#f7f5fb] disabled:opacity-50">
            Stay signed in
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy}
            className={cn('flex h-12 items-center justify-center gap-2 rounded-xl bg-[#9f1239] text-[14px] font-semibold text-white hover:bg-[#881337]', busy && 'opacity-80')}
          >
            {busy ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <LogOut size={16} aria-hidden="true" />}
            {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      }
    >
      <div className="px-4 py-4 text-[13.5px] leading-snug text-[#1A1428] sm:px-5">
        {pending.orders > 0 ? (
          <>
            <p className="rounded-xl bg-[#fdf3e3] px-3 py-2.5 font-medium text-[#7a4d0a]">
              {pending.orders === 1 ? 'An order' : `${pending.orders} orders`} in progress on this phone ({pending.lines} {pending.lines === 1 ? 'line' : 'lines'}) {pending.orders === 1 ? 'is' : 'are'} cleared. Place {pending.orders === 1 ? 'it' : 'them'} first if a shop is waiting.
            </p>
            <p className="mt-3 text-[#6b6480]">The shop you were ordering for is cleared too, so the next person who signs in on this phone starts clean.</p>
          </>
        ) : (
          <p className="text-[#6b6480]">The shop you were ordering for is cleared from this phone, so the next person who signs in starts clean.</p>
        )}
      </div>
    </Sheet>
  )
}
