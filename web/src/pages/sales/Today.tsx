import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, BookImage, Check, ChevronRight, ClipboardList, Copy, ExternalLink, Loader2, MessageCircle, PackageSearch, QrCode, Share2, Users } from 'lucide-react'
import { apiGet, apiPost } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { STATUS_LABEL, STATUS_TONE } from '@/pages/shop-ops/OrderActions'
import { Badge } from '@/components/ui/badge'
import { bhd3, firstName, greeting, monthName, relTime, useAuthedBlob, useShopMe, waLink } from './lib'

/**
 * /today — the salesman's home. What needs him now (orders waiting to be confirmed), how his
 * month is going (KPIs and the target bar), his link and QR one tap away, and the three things he
 * does all day: order for a shop, open his customers, open his orders.
 */

interface RestockRow {
  item_code: string
  display_name: string
  count: number
  phones: string[]
  first_at: string
  ids: number[]
  stock_qty?: number | null
  back_in_stock: boolean
}

interface OrderRow {
  id: number
  order_no: string
  status: string
  created_at?: string | null
  customer_name?: string | null
  customer_shop?: string | null
  customer_area?: string | null
  total_bhd?: number | null
  items?: number | null
  units?: number | null
}

export default function Today() {
  const { me } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const meQ = useShopMe()
  const newQ = useQuery({ queryKey: ['shop-orders', 'new', ''], queryFn: () => apiGet<{ orders: OrderRow[]; count: number }>('/shop/orders?status=new&limit=5'), refetchInterval: 60_000 })
  const progressQ = useQuery({ queryKey: ['shop-orders', 'confirmed,packed', ''], queryFn: () => apiGet<{ orders: OrderRow[]; count: number }>('/shop/orders?status=confirmed,packed&limit=3') })
  const [qrOpen, setQrOpen] = useState(false)
  const qc = useQueryClient()
  const restockQ = useQuery({ queryKey: ['shop-restock'], queryFn: () => apiGet<{ requests: RestockRow[] }>('/shop/restock'), staleTime: 60_000 })
  const resolveRestock = async (r: RestockRow) => {
    try {
      await apiPost('/shop/restock/resolve', { ids: r.ids })
      qc.invalidateQueries({ queryKey: ['shop-restock'] })
      toast('Marked as told', 'success')
    } catch {
      toast('Could not update', 'error')
    }
  }
  const { blobUrl: qr, loading: qrLoading } = useAuthedBlob(qrOpen ? meQ.data?.qr_url : null)

  const name = me?.full_name || meQ.data?.salesman?.name || ''
  const link = meQ.data?.link || ''
  const kpis = meQ.data?.kpis
  const tier = meQ.data?.focus?.target || null
  const today = useMemo(() => new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }), [])
  const newOrders = newQ.data?.orders || []
  const newCount = newQ.data?.count || 0

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
        {newCount > 0 && (
          <Link to="/shop-orders" className="hidden h-11 items-center gap-2 rounded-xl bg-primary px-4 text-[13px] font-semibold text-primary-foreground md:inline-flex">
            {newCount} to confirm <ArrowRight size={15} />
          </Link>
        )}
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:gap-6">
        <div className="space-y-4">
          {/* quick actions */}
          <div className="grid grid-cols-3 gap-2 md:gap-3">
            {[
              { to: '/shop', label: 'New order', hint: 'Order for a shop', icon: BookImage, primary: true },
              { to: '/customers', label: 'Customers', hint: 'Your book of shops', icon: Users },
              { to: '/shop-orders', label: 'Orders', hint: newCount ? `${newCount} waiting` : 'All stages', icon: ClipboardList },
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

          {/* needs you */}
          <section className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="flex items-center justify-between px-4 py-3">
              <h2 className="font-display text-[15px] font-bold">Waiting for you</h2>
              <Link to="/shop-orders" className="-mr-2 inline-flex h-10 items-center rounded-lg px-2 text-[12.5px] font-semibold text-primary hover:bg-muted">
                All orders
              </Link>
            </div>
            {newQ.isLoading ? (
              <div className="grid h-24 place-items-center text-muted-foreground">
                <Loader2 size={18} className="animate-spin" />
              </div>
            ) : newOrders.length === 0 ? (
              <p className="border-t border-border px-4 py-5 text-[13px] text-muted-foreground">Nothing waiting. New orders from your link land here.</p>
            ) : (
              <ul className="divide-y divide-border border-t border-border">
                {newOrders.map((o) => (
                  <li key={o.id}>
                    <button type="button" onClick={() => navigate(`/shop-orders?open=${o.id}`)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted">
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-[14px] font-semibold">{o.customer_shop || o.customer_name || o.order_no}</span>
                          <Badge tone={STATUS_TONE[o.status] || 'grey'}>{STATUS_LABEL[o.status] || o.status}</Badge>
                        </span>
                        <span className="mt-0.5 block truncate text-[12px] text-muted-foreground">
                          {[o.customer_area, o.items ? `${o.items} products` : null, o.units ? `${o.units} pcs` : null, relTime(o.created_at)].filter(Boolean).join(' · ')}
                        </span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block font-display text-[14px] font-bold tabular-nums">{bhd3(o.total_bhd)}</span>
                        <span className="block text-[11.5px] font-semibold text-primary">Confirm</span>
                      </span>
                      <ChevronRight size={16} className="shrink-0 text-muted-foreground" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {(progressQ.data?.count || 0) > 0 && (
              <Link to="/shop-orders?bucket=progress" className="flex items-center justify-between border-t border-border bg-muted/60 px-4 py-2.5 text-[12.5px] font-medium text-muted-foreground hover:text-foreground">
                <span>{progressQ.data!.count} in progress (confirmed / preparing)</span>
                <ChevronRight size={15} aria-hidden="true" />
              </Link>
            )}
          </section>

          {/* waiting for stock */}
          {(restockQ.data?.requests?.length || 0) > 0 && (
            <section className="overflow-hidden rounded-2xl border border-border bg-card">
              <div className="flex items-center gap-2 px-4 py-3">
                <PackageSearch size={16} className="text-primary" aria-hidden="true" />
                <h2 className="font-display text-[15px] font-bold">Waiting for stock</h2>
                <span className="text-[12px] text-muted-foreground">shops asked to be told</span>
              </div>
              <ul className="divide-y divide-border border-t border-border">
                {restockQ.data!.requests.slice(0, 8).map((r) => {
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
                      <button type="button" onClick={() => resolveRestock(r)} aria-label="Mark as told" title="Mark as told" className="grid h-10 w-10 place-items-center rounded-xl border border-border text-muted-foreground hover:bg-muted hover:text-foreground">
                        <Check size={16} aria-hidden="true" />
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}
        </div>

        <div className="space-y-4">
          {/* month */}
          <section className="rounded-2xl border border-border bg-card p-4">
            <h2 className="font-display text-[15px] font-bold">Your month</h2>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {[
                { k: 'Orders · 7 days', v: kpis?.orders_7d ?? 0 },
                { k: 'Orders · 30 days', v: kpis?.orders_30d ?? 0 },
                { k: 'Value · 30 days', v: bhd3(kpis?.value_30d_bhd) },
                { k: 'Shops · 30 days', v: kpis?.customers_30d ?? 0 },
              ].map((s) => (
                <div key={s.k} className="rounded-xl bg-muted px-3 py-2.5">
                  <div className="text-[11px] text-muted-foreground">{s.k}</div>
                  <div className="mt-0.5 font-display text-[18px] font-bold tabular-nums leading-tight">{meQ.isLoading ? '—' : s.v}</div>
                </div>
              ))}
            </div>
          </section>

          {/* tiered kickback — this month's Focus sales vs the rep's tier thresholds */}
          {tier ? (
            <section className="rounded-2xl border border-border bg-card p-4" aria-label="Kickback this month">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-display text-[15px] font-bold">Kickback · {monthName(tier.month)}</h2>
                <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-semibold',
                  tier.tier_reached > 0 ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
                  {tier.tier_reached > 0 ? `Tier ${tier.tier_reached} · ${Math.round(tier.kickback_pct * 100)}%` : 'Below Tier 1'}
                </span>
              </div>
              <div className="mt-2 flex items-baseline gap-1.5">
                <span className="font-display text-[22px] font-bold tabular-nums leading-none">{bhd3(tier.mtd_bhd)}</span>
                <span className="text-[12px] text-muted-foreground">sold this month</span>
              </div>
              <div className="relative mt-3 h-2 rounded-full bg-muted" role="progressbar" aria-valuenow={tier.progress_pct} aria-valuemin={0} aria-valuemax={100}>
                <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${tier.progress_pct}%` }} />
                {tier.tiers.map((t) => {
                  const top = tier.tiers[tier.tiers.length - 1].bhd || 1
                  return (
                    <span key={t.n} className={cn('absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full', tier.mtd_bhd >= t.bhd ? 'bg-primary-foreground/80' : 'bg-foreground/30')}
                      style={{ left: `calc(${(t.bhd / top) * 100}% - 1px)` }} title={`Tier ${t.n} · BHD ${t.bhd}`} />
                  )
                })}
              </div>
              <div className="mt-1.5 flex justify-between text-[10.5px] tabular-nums text-muted-foreground">
                {tier.tiers.map((t) => <span key={t.n}>T{t.n} {t.bhd.toLocaleString('en-US')}</span>)}
              </div>
              <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-[12.5px]">
                <span>
                  {tier.next_tier
                    ? <><b className="tabular-nums">{bhd3(tier.next_tier.gap_bhd)}</b> more for Tier {tier.next_tier.n} ({Math.round(tier.next_tier.pct * 100)}%)</>
                    : <b>Top tier reached</b>}
                </span>
                {tier.days_left != null && <span className="text-muted-foreground">{tier.days_left} day{tier.days_left === 1 ? '' : 's'} left</span>}
              </div>
              <div className="mt-1 text-[12px] text-muted-foreground">
                Kickback so far <b className="tabular-nums text-foreground">{bhd3(tier.kickback_bhd)}</b>
                {tier.data_through ? <> · sales to {tier.data_through}</> : null}
              </div>
            </section>
          ) : null}

          {/* my link */}
          <section className="rounded-2xl border border-border bg-card p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-[15px] font-bold">My link</h2>
              {meQ.data?.salesman?.referral_code && <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-foreground">/{meQ.data.salesman.referral_code}</span>}
            </div>
            {meQ.data && !meQ.data.salesman ? (
              <p className="mt-2 text-[12.5px] text-muted-foreground">{meQ.data.hint || 'Your login is not linked to a salesman yet — ask the office.'}</p>
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
    </div>
  )
}
