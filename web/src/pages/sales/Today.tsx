import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowRight, BookImage, Check, ChevronRight, ClipboardList, Copy, ExternalLink, Loader2, MessageCircle, PackageSearch, QrCode, Share2, Timer, Users } from 'lucide-react'
import { apiPost } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { Badge } from '@/components/ui/badge'
import { ageLabel, bhd3, bhdStr, dayLabel, firstName, greeting, monthName, relTime, useAuthedBlob, useToday, waLink, type MoneyStrip, type TodayData } from './lib'
import { ComingSoon } from './ComingSoon'
import { BasketsNotSent, DueThisWeek, LinkThisWeek } from './FollowUps'

/**
 * /today — the salesman's home, in ONE call (GET /shop/me/today, Sprint 5). It opens on the money
 * strip (this month, the tier, what the next tier is worth, how old the sales data is) and on "To
 * confirm" — the orders waiting for him, oldest first, their age, red once past the confirm time.
 * Then the three things he does all day (order for a shop, open his shops, open his orders), the
 * shops due this week with "Order again" and "Send restock link", baskets his link left behind,
 * stock shops asked for, and — in the right column — his link.
 */

function slaLabel(min?: number | null): string {
  if (!min) return ''
  return min % 60 === 0 ? `${min / 60} h` : `${min} min`
}

/** The top of Today: this month's figure and what one more push is worth (estimates until a statement is approved). */
function MoneyBand({ money }: { money: MoneyStrip }) {
  const pct = money.next_gap_bhd != null ? Math.max(0, Math.min(100, (Number(money.mtd_bhd) / (Number(money.mtd_bhd) + Number(money.next_gap_bhd) || 1)) * 100)) : 100
  return (
    <section aria-label="Your month" className="relative overflow-hidden rounded-3xl bg-[linear-gradient(135deg,#6D4091_0%,#824FAB_60%,#9A6BC2_100%)] p-4 text-white shadow-[0_18px_40px_-24px_rgba(109,64,145,.85)] lg:p-5">
      <div className="pointer-events-none absolute -right-10 -top-12 h-40 w-40 rounded-full bg-white/10 blur-2xl" aria-hidden="true" />
      <div className="relative flex flex-wrap items-center justify-between gap-2 text-[11.5px]">
        <span className="font-semibold text-white/85">
          {monthName(money.month)} · {money.tier > 0 ? `Tier ${money.tier} · ${(money.rate * 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}%` : 'Below Tier 1'}
        </span>
        {money.data_through && (
          <span className={cn('rounded-full px-2 py-0.5 font-semibold', money.stale ? 'bg-amber-300 text-amber-950' : 'bg-white/15 text-white/90')}>
            Sales to {dayLabel(money.data_through)}
            {money.stale && money.data_age_days != null ? ` · ${money.data_age_days} days old` : ''}
          </span>
        )}
      </div>
      <div className="relative mt-1.5 font-display text-[28px] font-extrabold leading-none tracking-[-0.02em] tabular-nums lg:text-[32px]">{bhdStr(money.mtd_bhd)}</div>
      <div className="relative mt-1 text-[12px] text-white/80">accessories sold this month{money.basis === 'net_ex_vat' ? ', ex-VAT' : ''}</div>
      <div className="relative mt-3 h-2 overflow-hidden rounded-full bg-white/20" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Progress to the next tier">
        <div className="h-full rounded-full bg-white transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      <div className="relative mt-3 rounded-2xl bg-white/12 px-3 py-2.5 text-[13.5px] leading-snug ring-1 ring-inset ring-white/15">
        {money.next_tier != null && money.next_gap_bhd != null ? (
          <>
            <b className="tabular-nums">{bhdStr(money.next_gap_bhd)} more</b> <span aria-hidden="true">→</span>
            <span className="sr-only"> earns </span> <b className="tabular-nums">+{bhdStr(money.next_gain_bhd)}</b> kickback at Tier {money.next_tier}
            {money.days_left != null ? <span className="text-white/75"> · {money.days_left} day{money.days_left === 1 ? '' : 's'} left</span> : null}
          </>
        ) : (
          <b>Top tier reached</b>
        )}
      </div>
      <div className="relative mt-2 text-[11px] text-white/75">
        Estimated kickback <b className="tabular-nums text-white">{bhdStr(money.kickback_bhd)}</b> · final after approval · {money.returns_deducted ? 'returns deducted' : 'returns not yet deducted'}
      </div>
    </section>
  )
}

function ToConfirm({ t }: { t: TodayData }) {
  const navigate = useNavigate()
  const sla = slaLabel(t.sla_min)
  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-card" aria-label="To confirm">
      <div className="flex items-center gap-2 px-4 py-3">
        <Timer size={16} className="text-primary" aria-hidden="true" />
        <h2 className="font-display text-[15px] font-bold">To confirm</h2>
        {t.waiting_count > 0 && <span className="rounded-full bg-primary px-2 py-0.5 text-[11px] font-bold tabular-nums text-primary-foreground">{t.waiting_count}</span>}
        <Link to="/shop-orders" className="-mr-2 ml-auto inline-flex h-10 items-center rounded-lg px-2 text-[12.5px] font-semibold text-primary hover:bg-muted">
          All orders
        </Link>
      </div>
      {t.errors?.waiting ? (
        <p className="border-t border-border px-4 py-5 text-[13px] text-destructive">The order queue did not load — open Orders.</p>
      ) : t.waiting.length === 0 ? (
        <p className="border-t border-border px-4 py-5 text-[13px] text-muted-foreground">Nothing waiting. New orders from your link land here, oldest first.</p>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {t.waiting.map((o) => (
            <li key={o.id}>
              <button type="button" onClick={() => navigate(`/shop-orders?open=${o.id}`)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted">
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-[14px] font-semibold">{o.customer_shop || o.customer_name || o.order_no}</span>
                    {o.order_kind === 'small' && <Badge tone="amber">Small order</Badge>}
                    {o.is_test && <Badge tone="grey">Test</Badge>}
                  </span>
                  <span className="mt-0.5 block truncate text-[12px] text-muted-foreground">
                    {[o.customer_area, o.items ? `${o.items} products` : null, o.units ? `${o.units} pcs` : null].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  <span className="block font-display text-[14px] font-bold tabular-nums">{bhdStr(o.total_bhd)}</span>
                  <span className={cn('mt-0.5 inline-flex items-center rounded-full px-1.5 py-px text-[11px] font-semibold tabular-nums', o.overdue ? 'bg-[#fdecef] text-[#9f1239]' : 'text-muted-foreground')}>
                    {ageLabel(o.age_min)}
                    {o.overdue && sla ? ` · over ${sla}` : ''}
                  </span>
                </span>
                <ChevronRight size={16} className="shrink-0 text-muted-foreground" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {t.waiting_count > t.waiting.length && (
        <Link to="/shop-orders" className="flex items-center justify-between border-t border-border px-4 py-2.5 text-[12.5px] font-semibold text-primary hover:bg-muted">
          {t.waiting_count - t.waiting.length} more waiting <ChevronRight size={15} aria-hidden="true" />
        </Link>
      )}
      {(t.in_progress || 0) > 0 && (
        <Link to="/shop-orders?bucket=confirmed" className="flex items-center justify-between border-t border-border bg-muted/60 px-4 py-2.5 text-[12.5px] font-medium text-muted-foreground hover:text-foreground">
          {/* the rep's flow is Received → Confirmed → Delivered: the storekeeper's stamps are never named here */}
          <span>{t.in_progress} confirmed, not yet delivered</span>
          <ChevronRight size={15} aria-hidden="true" />
        </Link>
      )}
      {sla && <p className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">Oldest first. Red once an order has waited more than {sla} of working time.</p>}
    </section>
  )
}

export default function Today() {
  const { me } = useAuth()
  const toast = useToast()
  const qc = useQueryClient()
  const todayQ = useToday()
  const t = todayQ.data
  const [qrOpen, setQrOpen] = useState(false)
  const resolveRestock = async (ids: number[]) => {
    try {
      await apiPost('/shop/restock/resolve', { ids })
      void qc.invalidateQueries({ queryKey: ['shop-today'] })
      toast('Marked as told', 'success')
    } catch {
      toast('Could not update', 'error')
    }
  }
  const meCard = t?.me || null
  const { blobUrl: qr, loading: qrLoading } = useAuthedBlob(qrOpen ? meCard?.qr_url : null)

  const name = me?.full_name || meCard?.salesman?.name || ''
  const link = meCard?.link || ''
  const kpis = meCard?.kpis
  const today = useMemo(() => new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }), [])
  const waiting = t?.waiting_count || 0

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link)
      toast('Link copied', 'success')
    } catch {
      toast('Could not copy', 'error')
    }
  }
  const share = async () => {
    try {
      if (navigator.share) await navigator.share({ title: 'YQ Marketplace', text: 'Order from YQ at trade prices:', url: link })
      else window.open(`https://wa.me/?text=${encodeURIComponent(`Order from YQ at trade prices: ${link}`)}`, '_blank', 'noreferrer')
    } catch {
      /* dismissed */
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-4 lg:px-8 lg:py-8">
      {/* greeting */}
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-[12.5px] text-muted-foreground">{today}</p>
          <h1 className="font-display text-[24px] font-bold leading-tight tracking-tight lg:text-[30px]">{greeting(firstName(name))}</h1>
        </div>
        {waiting > 0 && (
          <Link to="/shop-orders" className="hidden h-11 items-center gap-2 rounded-xl bg-primary px-4 text-[13px] font-semibold text-primary-foreground md:inline-flex">
            {waiting} to confirm <ArrowRight size={15} />
          </Link>
        )}
      </div>

      {todayQ.isLoading ? (
        <div className="grid h-60 place-items-center text-muted-foreground">
          <Loader2 size={20} className="animate-spin" aria-label="Loading your day" />
        </div>
      ) : todayQ.isError || !t ? (
        <div className="mt-5 rounded-2xl border border-border bg-card px-4 py-6 text-center">
          <p className="text-[13.5px] font-semibold">Your day did not load</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">The server did not answer. Check the connection and try again.</p>
          <button type="button" onClick={() => void todayQ.refetch()} className="mt-4 inline-flex h-11 items-center rounded-xl bg-primary px-5 text-[13px] font-semibold text-primary-foreground">
            Try again
          </button>
        </div>
      ) : (
        /* minmax(0,…) at every width: an `auto` phone column grows to the widest nowrap line (a long
           shop name + badges) and pushes every card past the screen edge */
        <div className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:gap-6">
          <div className="min-w-0 space-y-4">
            {t.hint && <p className="rounded-2xl border border-border bg-card px-4 py-4 text-[13px] text-muted-foreground">{t.hint}</p>}
            {t.money && <MoneyBand money={t.money} />}
            <ToConfirm t={t} />

            {/* quick actions */}
            <div className="grid grid-cols-3 gap-2 md:gap-3">
              {[
                { to: '/shop', label: 'New order', hint: 'Order for a shop', icon: BookImage, primary: true },
                { to: '/customers', label: 'Shops', hint: 'Your book', icon: Users },
                { to: '/shop-orders', label: 'Orders', hint: waiting ? `${waiting} waiting` : 'All stages', icon: ClipboardList },
              ].map((a) => (
                <Link key={a.to} to={a.to} className={cn('group flex min-h-[5.5rem] flex-col justify-between rounded-2xl border p-3 transition-transform duration-150 hover:-translate-y-0.5 md:p-4', a.primary ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground')}>
                  <a.icon size={20} aria-hidden="true" className={a.primary ? 'text-primary-foreground/90' : 'text-primary'} />
                  <span>
                    <span className="block text-[13.5px] font-bold leading-tight md:text-[15px]">{a.label}</span>
                    <span className={cn('block text-[11px] leading-tight md:text-[12px]', a.primary ? 'text-primary-foreground/75' : 'text-muted-foreground')}>{a.hint}</span>
                  </span>
                </Link>
              ))}
            </div>

            {/* due this week — the rep's Focus book by its own rhythm, with Order again and the restock link */}
            {t.errors?.due ? (
              <p className="rounded-2xl border border-border bg-card px-4 py-4 text-[12.5px] text-muted-foreground">Due this week did not load just now — it comes back on the next refresh.</p>
            ) : (
              <DueThisWeek due={t.due} counts={t.due_counts} dataThrough={t.due_data_through} hint={t.due_hint} />
            )}

            {/* baskets opened on my link and never sent (R3a) */}
            <BasketsNotSent data={t.baskets} />

            {/* waiting for stock */}
            {t.restock.length > 0 && (
              <section className="overflow-hidden rounded-2xl border border-border bg-card">
                <div className="flex items-center gap-2 px-4 py-3">
                  <PackageSearch size={16} className="text-primary" aria-hidden="true" />
                  <h2 className="font-display text-[15px] font-bold">Waiting for stock</h2>
                  <span className="text-[12px] text-muted-foreground">shops asked to be told</span>
                </div>
                <ul className="divide-y divide-border border-t border-border">
                  {t.restock.slice(0, 8).map((r) => {
                    const wa = waLink(r.phones[0], `Hello, ${r.display_name} (${r.item_code}) is back in stock at YQ. Shall I add it to your next order?`)
                    return (
                      <li key={r.item_code} className="flex items-center gap-3 px-4 py-2.5">
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2">
                            <span className="truncate text-[13.5px] font-semibold">{r.display_name}</span>
                            {r.back_in_stock ? <Badge tone="green">Back in stock</Badge> : <Badge tone="grey">Still out</Badge>}
                          </span>
                          <span className="block text-[11.5px] text-muted-foreground">
                            {r.item_code} · {r.count} {r.count === 1 ? 'shop' : 'shops'} · since {relTime(r.first_at)}
                          </span>
                        </span>
                        {wa && r.back_in_stock && (
                          <a href={wa} target="_blank" rel="noreferrer" aria-label="WhatsApp the shop" className="grid h-10 w-10 place-items-center rounded-xl border border-border text-[#1d9e50] hover:bg-muted">
                            <MessageCircle size={16} aria-hidden="true" />
                          </a>
                        )}
                        <button type="button" onClick={() => resolveRestock(r.ids)} aria-label="Mark as told" title="Mark as told" className="grid h-10 w-10 place-items-center rounded-xl border border-border text-muted-foreground hover:bg-muted hover:text-foreground">
                          <Check size={16} aria-hidden="true" />
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </section>
            )}

            {/* coming soon: its own calls, mounted after Today's one call has answered */}
            <ComingSoon link={link} />
          </div>

          <div className="min-w-0 space-y-4">
            {/* marketplace, last 30 days */}
            <section className="rounded-2xl border border-border bg-card p-4">
              <h2 className="font-display text-[15px] font-bold">Marketplace · last 30 days</h2>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {[
                  { k: 'Orders · 7 days', v: kpis?.orders_7d ?? 0 },
                  { k: 'Orders · 30 days', v: kpis?.orders_30d ?? 0 },
                  { k: 'Value · 30 days', v: bhd3(kpis?.value_30d_bhd) },
                  { k: 'Shops · 30 days', v: kpis?.customers_30d ?? 0 },
                ].map((s) => (
                  <div key={s.k} className="rounded-xl bg-muted px-3 py-2.5">
                    <div className="text-[11px] text-muted-foreground">{s.k}</div>
                    <div className="mt-0.5 font-display text-[18px] font-bold tabular-nums leading-tight">{s.v}</div>
                  </div>
                ))}
              </div>
            </section>

            {/* my link this week — the rep's own 7-day funnel (R3a) */}
            <LinkThisWeek data={t.link_week} error={t.errors?.link_week} onRetry={() => void todayQ.refetch()} retrying={todayQ.isFetching} />

            {/* my link */}
            <section className="rounded-2xl border border-border bg-card p-4">
              <div className="flex items-center justify-between">
                <h2 className="font-display text-[15px] font-bold">My link</h2>
                {meCard?.salesman?.referral_code && <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-foreground">/{meCard.salesman.referral_code}</span>}
              </div>
              {meCard && !meCard.salesman ? (
                <p className="mt-2 text-[12.5px] text-muted-foreground">{meCard.hint || 'Your login is not linked to a salesman yet — ask the office.'}</p>
              ) : (
                <>
                  <p className="mt-1 truncate text-[12.5px] text-muted-foreground">{link.replace(/^https?:\/\//, '') || '…'}</p>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <button type="button" onClick={share} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#25D366] text-[13px] font-semibold text-white">
                      <MessageCircle size={16} aria-hidden="true" /> Share
                    </button>
                    <button type="button" onClick={copy} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border bg-card text-[13px] font-semibold">
                      <Copy size={15} aria-hidden="true" /> Copy
                    </button>
                    <button type="button" onClick={() => setQrOpen((v) => !v)} aria-expanded={qrOpen} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border bg-card text-[13px] font-semibold">
                      <QrCode size={15} aria-hidden="true" /> QR code
                    </button>
                    <a href={link || '#'} target="_blank" rel="noreferrer" className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border bg-card text-[13px] font-semibold">
                      <ExternalLink size={15} aria-hidden="true" /> Open
                    </a>
                  </div>
                  {qrOpen && (
                    <div className="mt-3 grid place-items-center rounded-xl border border-border bg-white p-3">
                      {qr ? <img src={qr} alt="QR code for my link" width={220} height={220} className="h-[220px] w-[220px]" /> : qrLoading ? <Loader2 size={18} className="my-24 animate-spin text-muted-foreground" /> : <span className="my-24 text-[12px] text-muted-foreground">QR unavailable</span>}
                      <p className="mt-2 text-center text-[11.5px] text-muted-foreground">Show this in the shop — they scan and order from your storefront.</p>
                    </div>
                  )}
                </>
              )}
            </section>
            <p className="px-1 text-[11.5px] text-muted-foreground">
              Share on WhatsApp opens your phone's share sheet; on a laptop it opens WhatsApp Web. <Share2 size={11} className="inline" aria-hidden="true" />
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
