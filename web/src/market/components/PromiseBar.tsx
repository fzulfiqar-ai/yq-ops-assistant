import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Download } from 'lucide-react'
import type { MarketPromise as PromiseRow } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { bhd, money } from '../lib/format'
import { PROMISE_ICON_FALLBACK, PROMISE_ICONS } from '../lib/icons'
import { canPromptInstall, isStandalone, onInstallChange, promptInstall } from '../lib/install'
import { locale, S } from '../strings'

/**
 * The YQ promise — the wholesale answer to "free shipping": only claims the data can back (free
 * delivery, trade prices, real warehouse stock, every order confirmed by a rep), edited by the office
 * in settings (`shop_market_promises`). Desktop wears them as a dark plum utility bar above the
 * header, with "Add YQ app" at its end; phones as a thin light strip that Home places.
 *
 * The stock line says "real", never "live" or "real-time": the catalog is a dated snapshot and the
 * footer prints its date, so a freshness claim here would contradict the same page (app/shop.py
 * SETTING_DEFAULTS, and tests/test_shop.py guards every promise against it in en and ar).
 *
 * The phone strip WRAPS (it never scrolls): the four promises measure ~660px, so a single row sliced
 * the stock claim mid-word at every phone width and hid "Every order confirmed by your rep"
 * entirely — the two most wholesale-specific claims, on the first screen. Tidy rows say every one
 * (with the wholesale minimum leading, three rows on the narrowest phones).
 */

/**
 * The office's promises, led by the wholesale minimum whenever the shop has one: a merchant learns
 * it here, before the first add, instead of in the restock after it. The amount is the payload's
 * settings.min_order_bhd, never a number typed into the page; a promise the office already keyed
 * 'minimum' wins over the derived one.
 */
function usePromises(): PromiseRow[] {
  const { settings } = useMarket()
  return useMemo(() => {
    const rows = (settings.promises || []).filter((p) => p && p.en)
    const min = Number(settings.min_order_bhd) || 0
    if (min <= 0 || rows.some((p) => p.key === 'minimum')) return rows
    return [{ key: 'minimum', en: S.promise.minimum(bhd(min)), ar: S.promise.minimumAr(money(min)), icon: 'package', to: '/about#trade' }, ...rows]
  }, [settings])
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
      {/* 40px under a mouse — the slim utility bar, but tall enough to hold a 36px "Add YQ app"
          button inside it (the hard tap floor, §0.8: it used to be 28px); on a touch screen (a
          tablet in landscape gets this shell) the row opens to 48px so the button can be a full
          44px target */}
      <div className="container-m flex h-10 items-center text-xs [@media(pointer:coarse)]:h-12">
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
              // The bar holds four promises at 1024 without cutting a word; with the minimum leading
              // there are five, so below xl the fifth waits for the width (every one of them was
              // being cut mid-word at 1024) — it is back from 1280, where all five fit whole.
              <Fragment key={p.key}>
                {i > 0 && <li aria-hidden="true" className={cn('h-3 w-px shrink-0 bg-white/15', i >= 4 && 'hidden xl:block')} />}
                <li className={cn('min-w-0', i >= 4 && 'hidden xl:block')}>
                  {p.to ? (
                    // `hit`: the text is 16px tall; on a touch screen the band is 48px and the
                    // link's invisible hit area takes 44 of it (the look is unchanged)
                    <Link to={p.to} className={cn('hit relative inline-flex max-w-full items-center gap-1.5 transition-colors duration-1 ease-m hover:text-white', onDark)}>
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
          <button type="button" onClick={install} className={cn('ms-auto inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full bg-white/10 px-3.5 font-semibold text-white transition-colors duration-1 ease-m hover:bg-white/20 [@media(pointer:coarse)]:h-11', onDark)}>
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
    <ul className={cn('flex flex-wrap items-center gap-x-3 gap-y-1.5 text-2xs font-medium text-ink-2', className)} aria-label={S.promise.title}>
      {promises.map((p) => {
        const Icon = iconFor(p)
        return (
          <li key={p.key} className="inline-flex max-w-full items-center gap-1">
            <Icon size={12} className="shrink-0 text-plum" aria-hidden="true" />
            {text(p)}
          </li>
        )
      })}
    </ul>
  )
}
