import { useNavigate } from 'react-router-dom'
import { Link } from 'react-router-dom'
import { CalendarClock, ChevronRight, Link2, MessageCircle, Phone, ShoppingBag, ShoppingCart } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { bhdStr, dayLabel, logFollowupTap, relTime, setSelectedCustomer, telLink, useBaskets, useFollowups, useLinkWeek, waLink, type FollowupShop } from './lib'

/**
 * The rep's follow-up cards (release R3a): "Due this week" (the top of his ranked Focus book),
 * "Baskets from your link not sent" (adds on his link with no order) and "My link this week".
 * Everything is computed server-side from Focus history and the marketplace funnel — the copy
 * shows a cadence and an age, never a promised date. Taps are logged for measurement only.
 */

const ICON = 'grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-border bg-card hover:bg-muted'

/** One shop row, shared by the Today card and the Customers Due / Lapsed views. */
export function FollowupRow({ r, kind, compact = false }: { r: FollowupShop; kind: 'due' | 'lapsed'; compact?: boolean }) {
  const navigate = useNavigate()
  const wa = waLink(r.phone, r.wa_text || `Hello, YQ here. Shall I bring your usual order on my next round?`)
  const tel = telLink(r.phone)
  const orderFor = () => {
    logFollowupTap(r.shop, kind, 'order', r.rank)
    setSelectedCustomer({ name: r.shop, shop: r.shop, phone: r.phone || null })
    navigate('/shop')
  }
  return (
    <li className={cn('px-4', compact ? 'py-2.5' : 'py-3')}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-semibold leading-tight">{r.shop}</span>
            {kind === 'lapsed' ? <Badge tone="grey">Win back</Badge> : r.overdue_ratio >= 1.5 ? <Badge tone="amber">Overdue</Badge> : <Badge tone="accent">Due</Badge>}
            {r.holdout ? <Badge tone="rose">Holdout</Badge> : null}
          </div>
          <div className="mt-0.5 text-[12px] text-muted-foreground">{r.why}</div>
          <div className="mt-0.5 text-[11.5px] tabular-nums text-muted-foreground">
            {Number(r.monthly_value_bhd) > 0 ? `~${bhdStr(r.monthly_value_bhd)}/month` : 'No sales in 180 days'}
            {r.gap_source === 'own' ? '' : ' · cadence not known yet'}
          </div>
          {r.top_skus.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {r.top_skus.map((s) => (
                <span key={s.item_code} title={`${s.display_name} · bought ${s.times_bought} times · usual qty ${s.median_qty}`} className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] tabular-nums">
                  {s.item_code} <span className="text-muted-foreground">×{s.median_qty}</span>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-1.5">
          <button type="button" onClick={orderFor} aria-label={`New order for ${r.shop}`} title="New order" className={cn(ICON, 'text-primary')}>
            <ShoppingBag size={16} aria-hidden="true" />
          </button>
          <a href={wa || '#'} target="_blank" rel="noreferrer" onClick={() => wa && logFollowupTap(r.shop, kind, 'wa', r.rank)} aria-label={wa ? `WhatsApp ${r.shop}` : 'No WhatsApp number on file'} title={wa ? 'WhatsApp' : 'No number on file'} className={cn(ICON, 'text-[#1d9e50]', !wa && 'pointer-events-none opacity-40')}>
            <MessageCircle size={16} aria-hidden="true" />
          </a>
          <a href={tel || '#'} onClick={() => tel && logFollowupTap(r.shop, kind, 'call', r.rank)} aria-label={tel ? `Call ${r.shop}` : 'No phone number on file'} title={tel ? 'Call' : 'No number on file'} className={cn(ICON, !tel && 'pointer-events-none opacity-40')}>
            <Phone size={16} aria-hidden="true" />
          </a>
        </div>
      </div>
    </li>
  )
}

/** Today: the top 5 due shops. Hidden until the rep's login carries a Focus name. */
export function DueThisWeek() {
  const q = useFollowups()
  const data = q.data
  if (q.isLoading) return null
  if (!data || data.hint) return null
  const due = data.due.slice(0, 5)
  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-card" aria-label="Due this week">
      <div className="flex items-center gap-2 px-4 py-3">
        <CalendarClock size={16} className="text-primary" aria-hidden="true" />
        <h2 className="font-display text-[15px] font-bold">Due this week</h2>
        <span className="text-[12px] text-muted-foreground">
          {data.counts?.due ? `${data.counts.due} shop${data.counts.due === 1 ? '' : 's'} by their usual rhythm` : 'from your Focus sales'}
        </span>
        <Link to="/customers?view=due" className="-mr-2 ml-auto inline-flex h-10 items-center gap-1 rounded-lg px-2 text-[12.5px] font-semibold text-primary hover:bg-muted">
          All due <ChevronRight size={14} aria-hidden="true" />
        </Link>
      </div>
      {due.length === 0 ? (
        <p className="border-t border-border px-4 py-5 text-[13px] text-muted-foreground">
          Nothing due right now. {data.counts?.lapsed ? <Link to="/customers?view=lapsed" className="font-semibold text-primary">{data.counts.lapsed} to win back</Link> : null}
        </p>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {due.map((r) => <FollowupRow key={r.shop} r={r} kind="due" />)}
        </ul>
      )}
      <p className="border-t border-border bg-muted/60 px-4 py-2 text-[11px] text-muted-foreground">
        Ranked by how overdue a shop is against its own rhythm and what it usually buys per month
        {data.data_through ? ` · Focus sales to ${dayLabel(data.data_through)}` : ''}. Suggestions, not promises — nobody knows when a shop reorders.
      </p>
    </section>
  )
}

/** Today: baskets started on the rep's link and never sent (7 days). Codes and value only. */
export function BasketsNotSent() {
  const q = useBaskets()
  const data = q.data
  if (!data || data.hint || !data.baskets.length) return null
  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-card" aria-label="Baskets from your link not sent">
      <div className="flex items-center gap-2 px-4 py-3">
        <ShoppingCart size={16} className="text-primary" aria-hidden="true" />
        <h2 className="font-display text-[15px] font-bold">Baskets from your link not sent</h2>
        <span className="text-[12px] text-muted-foreground">last {data.days} days · {bhdStr(data.value_bhd)}</span>
      </div>
      <ul className="divide-y divide-border border-t border-border">
        {data.baskets.map((b) => (
          <li key={b.basket} className="px-4 py-2.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[12px] text-muted-foreground">
                {b.items_count} product{b.items_count === 1 ? '' : 's'} · {b.units} pcs · {relTime(b.last_activity)}
              </span>
              <span className="font-display text-[14px] font-bold tabular-nums">{bhdStr(b.value_bhd)}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {b.items.map((it) => (
                <span key={it.item_code} title={it.display_name} className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] tabular-nums">
                  {it.item_code} <span className="text-muted-foreground">×{it.qty}</span>
                </span>
              ))}
            </div>
          </li>
        ))}
      </ul>
      <p className="border-t border-border bg-muted/60 px-4 py-2 text-[11px] text-muted-foreground">
        A shop that opened your link, added these and did not send. You cannot see who it is — mention the items on your next round.
      </p>
    </section>
  )
}

/** Today (right column): the rep's own funnel over 7 days. */
export function LinkThisWeek() {
  const q = useLinkWeek()
  const d = q.data
  if (!d || d.hint) return null
  const stats = [
    { k: 'Visits', v: d.sessions ?? 0 },
    { k: 'Added to basket', v: d.adds ?? 0 },
    { k: 'Orders', v: d.orders ?? 0 },
    { k: 'Value', v: bhdStr(d.value_bhd) },
  ]
  return (
    <section className="rounded-2xl border border-border bg-card p-4" aria-label="My link this week">
      <div className="flex items-center gap-2">
        <Link2 size={16} className="text-primary" aria-hidden="true" />
        <h2 className="font-display text-[15px] font-bold">My link this week</h2>
        <span className="text-[12px] text-muted-foreground">{d.days} days</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {stats.map((s) => (
          <div key={s.k} className="rounded-xl bg-muted px-3 py-2.5">
            <div className="text-[11px] text-muted-foreground">{s.k}</div>
            <div className="mt-0.5 font-display text-[18px] font-bold tabular-nums leading-tight">{s.v}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[11.5px] text-muted-foreground">
        {d.conversion_pct != null ? `${d.conversion_pct}% of visits ordered` : 'No visits yet'}
        {d.shares ? ` · shared ${d.shares} time${d.shares === 1 ? '' : 's'}` : ''}
        {d.top_products?.length ? ` · most ordered ${d.top_products[0].item_code}` : ''}
      </div>
    </section>
  )
}
