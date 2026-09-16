import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Package, ShieldCheck, Truck } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { readCustomer, rememberedOrders } from '../lib/device'
import { isStandalone } from '../lib/install'
import { S } from '../strings'

/**
 * The first-open moment — the marketplace's answer to the ops portal's plum "YQ Intelligence"
 * screen. One idea, said once: your shelf, refilled in one tap. Mark → brand line → the idea →
 * the three promises, 1.4 s, tap to skip, once per device per 30 days. Never under reduced
 * motion and never on a PWA cold start (the OS splash already covers those). Returning
 * merchants see the personal version with one button: refill my shelf.
 */
const KEY = 'yq-splash-seen'
const DAYS = 30
const HOLD_MS = 1400

function shouldShow(): boolean {
  try {
    if (isStandalone()) return false
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false
    const seen = Number(localStorage.getItem(KEY) || 0)
    return Date.now() - seen > DAYS * 864e5
  } catch {
    return false
  }
}

export function Splash() {
  const { settings, recognized } = useMarket()
  const navigate = useNavigate()
  const [show, setShow] = useState(shouldShow)
  const [leaving, setLeaving] = useState(false)
  const shop = readCustomer().shop || readCustomer().name
  const returning = recognized && rememberedOrders().length > 0
  const promises = (settings.promises || []).slice(0, 3)

  useEffect(() => {
    if (!show) return
    try {
      localStorage.setItem(KEY, String(Date.now()))
    } catch {
      /* private mode */
    }
    const t = window.setTimeout(() => setLeaving(true), returning ? HOLD_MS + 1200 : HOLD_MS)
    return () => window.clearTimeout(t)
  }, [show, returning])

  useEffect(() => {
    if (!leaving) return
    const t = window.setTimeout(() => setShow(false), 320)
    return () => window.clearTimeout(t)
  }, [leaving])

  if (!show) return null
  const dismiss = () => setLeaving(true)
  const icons = [Truck, Package, ShieldCheck]

  return (
    <div
      role="dialog"
      aria-label={S.splash.idea}
      onClick={dismiss}
      className={cn('fixed inset-0 z-[80] flex flex-col items-center justify-center bg-plum-ink px-6 text-center text-white transition-opacity duration-3 ease-m', leaving ? 'pointer-events-none opacity-0' : 'opacity-100')}
    >
      <img src="/yq-icon-192.png" alt="" width={72} height={72} className="h-[72px] w-[72px] rounded-2xl shadow-3 anim-pop" />
      <h1 className="mt-6 font-display text-[28px] font-extrabold leading-[1.05] tracking-tight anim-fade-in sm:text-4xl" style={{ animationDelay: '120ms' }}>
        {S.splash.brand}
      </h1>
      <p className="mt-3 max-w-sm text-base text-white/85 anim-fade-in" style={{ animationDelay: '260ms' }}>
        {returning && shop ? S.splash.welcome(shop) : S.splash.idea}
      </p>
      {returning ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            setLeaving(true)
            navigate('/quick?load=regular')
          }}
          className="mt-7 inline-flex h-12 items-center gap-2 rounded-md bg-white px-5 text-base font-semibold text-plum-ink anim-fade-in"
          style={{ animationDelay: '400ms' }}
        >
          {S.splash.refill} <ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />
        </button>
      ) : (
        <ul className="mt-7 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-sm text-white/80 anim-fade-in" style={{ animationDelay: '400ms' }}>
          {promises.map((p, i) => {
            const Icon = icons[i % icons.length]
            return (
              <li key={p.key} className="inline-flex items-center gap-1.5">
                <Icon size={14} aria-hidden="true" /> {p.en}
              </li>
            )
          })}
        </ul>
      )}
      <span className="absolute bottom-6 text-xs text-white/50" style={{ bottom: 'calc(24px + env(safe-area-inset-bottom, 0px))' }}>
        {S.splash.skip}
      </span>
    </div>
  )
}
