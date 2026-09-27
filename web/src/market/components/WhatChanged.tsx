import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import type { OrderStatusPayload } from '@/lib/shopApi'
import { bhd } from '../lib/format'
import { changeText, orderChanges } from '../lib/orderChanges'
import { S } from '../strings'
import { Ltr } from '../ui/Ltr'

/**
 * "What changed" (R7d, plan §5 / §12): ONE tile at the top of the tracking page once the rep's
 * confirmation (or the delivery) differs from what the shop asked for — each changed line with its
 * numbers and the public reason, then the order total as ordered → as it stands. Nothing here is
 * computed that the payload does not carry (lib/orderChanges.ts); a cancelled order has no tile.
 */
export function WhatChanged({ data, className }: { data: OrderStatusPayload; className?: string }) {
  const ch = useMemo(() => orderChanges(data), [data])
  if (!ch.rows.length) return null
  return (
    <section aria-labelledby="yq-what-changed" className={cn('rounded-lg border border-warn/20 bg-warn-soft p-4', className)}>
      <h2 id="yq-what-changed" className="font-display text-sm font-bold text-ink">
        {S.track.whatChanged}
      </h2>
      <ul className="mt-2 divide-y divide-warn/15">
        {ch.rows.map((r) => {
          const reason = r.reason ? S.track.reasons[r.reason] || null : null
          return (
            <li key={r.key} className="py-2 first:pt-0 last:pb-0">
              <div className="text-sm font-semibold text-ink">
                <Ltr>{r.name}</Ltr>
              </div>
              <div className="mt-0.5 text-xs tnum text-ink-2">{changeText(S.track, r)}</div>
              {reason && <div className="mt-1 inline-flex rounded-xs bg-surface/80 px-1.5 py-0.5 text-2xs font-semibold text-warn">{reason}</div>}
            </li>
          )
        })}
      </ul>
      {ch.totalChanged && (
        <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-t border-warn/20 pt-2.5 text-sm">
          <span className="text-ink-2">{S.track.changeTotal}</span>
          <span className="tnum text-ink">
            <span className="text-ink-2">{bhd(ch.before)}</span> {S.track.changeArrow} <b className="font-display font-extrabold">{bhd(ch.after)}</b>
          </span>
        </div>
      )}
    </section>
  )
}
