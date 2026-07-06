import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE } from '@/lib/api'
import { Logo } from '@/components/Logo'

/** Public unsubscribe landing (linked from every outreach email — PDPL).
 *  Hitting the page records the opt-out; no further action needed. */
export default function OptOut() {
  const { token } = useParams()
  const [state, setState] = useState<'working' | 'done' | 'bad'>('working')

  useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/public/optout/${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? setState('done') : setState('bad')))
      .catch(() => setState('bad'))
  }, [token])

  return (
    <div className="grid min-h-screen place-items-center bg-[#faf9fc] px-4 text-center">
      <div className="max-w-sm">
        <Logo className="mx-auto h-14 w-14 rounded-2xl" />
        {state === 'working' && <p className="mt-4 text-sm text-[#6b6480]">One moment…</p>}
        {state === 'done' && (
          <>
            <h1 className="mt-4 font-display text-lg font-bold text-[#1a1430]">You're unsubscribed ✓</h1>
            <p className="mt-2 text-sm text-[#6b6480]">
              YQ Bahrain won't send you further marketing messages. You can always reach us
              directly on WhatsApp — we're happy to help anytime.
            </p>
          </>
        )}
        {state === 'bad' && (
          <p className="mt-4 text-sm text-[#6b6480]">
            This link is not valid. If you'd like to stop receiving messages, just reply
            "STOP" to any of our messages.
          </p>
        )}
      </div>
    </div>
  )
}
