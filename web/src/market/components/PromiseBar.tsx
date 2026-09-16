import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Download, Package, ShieldCheck, Truck, type LucideIcon } from 'lucide-react'
import type { MarketPromise as PromiseRow } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { canPromptInstall, isStandalone, onInstallChange, promptInstall } from '../lib/install'
import { locale, S } from '../strings'

/**
 * The YQ promise — our answer to "free shipping" and "fast delivery": only claims the data can
 * back (free delivery, no minimum, live stock), edited by the office in settings. Desktop shows
 * them as a quiet utility bar above the header; phones as a trust strip under the home search.
 */

const ICONS: Record<string, LucideIcon> = { truck: Truck, package: Package, pulse: Activity, shield: ShieldCheck }

function usePromises(): PromiseRow[] {
  const { settings } = useMarket()
  return (settings.promises || []).filter((p) => p && p.en)
}

function text(p: PromiseRow): string {
  return (locale.lang === 'ar' && p.ar) || p.en
}

function useInstallable(): [boolean, () => void] {
  const [can, setCan] = useState(() => canPromptInstall() && !isStandalone())
  useEffect(() => onInstallChange(() => setCan(canPromptInstall() && !isStandalone())), [])
  return [can, () => void promptInstall()]
}

export function PromiseBar() {
  const promises = usePromises()
  const [installable, install] = useInstallable()
  if (!promises.length && !installable) return null
  return (
    <div className="hidden border-b border-line-2 bg-surface text-ink-2 lg:block" aria-label={S.promise.title}>
      <div className="container-m flex h-9 items-center gap-6 text-xs">
        {promises.map((p) => {
          const Icon = ICONS[p.icon || ''] || ShieldCheck
          const inner = (
            <>
              <Icon size={14} className="text-plum" aria-hidden="true" />
              <span className="font-medium">{text(p)}</span>
            </>
          )
          return p.to ? (
            <Link key={p.key} to={p.to} className="inline-flex items-center gap-1.5 rounded-sm hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              {inner}
            </Link>
          ) : (
            <span key={p.key} className="inline-flex items-center gap-1.5">
              {inner}
            </span>
          )
        })}
        {installable && (
          <button type="button" onClick={install} className="ms-auto inline-flex items-center gap-1.5 font-semibold text-plum hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
            <Download size={14} aria-hidden="true" /> {S.promise.app}
          </button>
        )}
      </div>
    </div>
  )
}

export function PromiseStrip({ className }: { className?: string }) {
  const promises = usePromises()
  if (!promises.length) return null
  return (
    <ul className={cn('flex items-center gap-x-3 gap-y-1 overflow-x-auto text-2xs font-medium text-ink-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden', className)} aria-label={S.promise.title}>
      {promises.map((p) => {
        const Icon = ICONS[p.icon || ''] || ShieldCheck
        return (
          <li key={p.key} className="inline-flex shrink-0 items-center gap-1">
            <Icon size={12} className="text-plum" aria-hidden="true" />
            {text(p)}
          </li>
        )
      })}
    </ul>
  )
}
