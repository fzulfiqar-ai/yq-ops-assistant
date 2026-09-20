import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Download } from 'lucide-react'
import type { MarketPromise as PromiseRow } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { PROMISE_ICON_FALLBACK, PROMISE_ICONS } from '../lib/icons'
import { canPromptInstall, isStandalone, onInstallChange, promptInstall } from '../lib/install'
import { locale, S } from '../strings'

/**
 * The YQ promise — the wholesale answer to "free shipping": only claims the data can back (free
 * delivery, trade prices, live warehouse stock, every order confirmed by a rep), edited by the office
 * in settings (`shop_market_promises`). Desktop wears them as a dark plum utility bar above the
 * header, with "Add YQ app" at its end; phones as a thin light strip that Home places.
 */

function usePromises(): PromiseRow[] {
  const { settings } = useMarket()
  return (settings.promises || []).filter((p) => p && p.en)
}

function text(p: PromiseRow): string {
  return (locale.lang === 'ar' && p.ar) || p.en
}

function iconFor(p: PromiseRow) {
  return PROMISE_ICONS[(p.icon || '').trim().toLowerCase()] || PROMISE_ICON_FALLBACK
}

function useInstallable(): [boolean, () => void] {
  const [can, setCan] = useState(() => canPromptInstall() && !isStandalone())
  useEffect(() => onInstallChange(() => setCan(canPromptInstall() && !isStandalone())), [])
  return [can, () => void promptInstall()]
}

const onDark = 'rounded-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 focus-visible:ring-offset-2 focus-visible:ring-offset-plum-ink'

export function PromiseBar() {
  const promises = usePromises()
  const [installable, install] = useInstallable()
  if (!promises.length && !installable) return null
  return (
    <div className="hidden bg-plum-ink text-white/90 lg:block">
      <div className="container-m flex h-9 items-center text-xs">
        <ul className="flex min-w-0 items-center gap-4 xl:gap-5" aria-label={S.promise.title}>
          {promises.map((p, i) => {
            const Icon = iconFor(p)
            const inner = (
              <>
                <Icon size={14} strokeWidth={1.9} className="shrink-0 text-tile-lilac" aria-hidden="true" />
                <span className="truncate font-medium">{text(p)}</span>
              </>
            )
            return (
              <Fragment key={p.key}>
                {i > 0 && <li aria-hidden="true" className="h-3 w-px shrink-0 bg-white/15" />}
                <li className="min-w-0">
                  {p.to ? (
                    <Link to={p.to} className={cn('inline-flex max-w-full items-center gap-1.5 transition-colors duration-1 ease-m hover:text-white', onDark)}>
                      {inner}
                    </Link>
                  ) : (
                    <span className="inline-flex max-w-full items-center gap-1.5">{inner}</span>
                  )}
                </li>
              </Fragment>
            )
          })}
        </ul>
        {installable && (
          <button type="button" onClick={install} className={cn('ms-auto inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full bg-white/10 px-3 font-semibold text-white transition-colors duration-1 ease-m hover:bg-white/20', onDark, 'rounded-full')}>
            <Download size={13} strokeWidth={2} aria-hidden="true" /> {S.promise.app}
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
        const Icon = iconFor(p)
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
