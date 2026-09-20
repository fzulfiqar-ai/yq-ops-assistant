import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronRight, Store, Zap } from 'lucide-react'
import type { MyOrderSummary } from '@/lib/shopApi'
import { RepCard } from '../components/RepCard'
import { EmptyState } from '../components/States'
import { useMarket, useOrder } from '../MarketContext'
import { rememberedOrders } from '../lib/device'
import { bhd, fmtDate } from '../lib/format'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'

/** /orders — every order this phone placed (tokens are the capability; nothing else is listed). */
export default function MyOrdersPage() {
  const navigate = useNavigate()
  const { rep } = useMarket()
  const { myOrders, refreshMyOrders } = useOrder()
  const known = rememberedOrders()
  usePageTitle(S.orders.title, true, `${S.orders.title} · ${S.brand}`)
  useEffect(() => {
    refreshMyOrders()
  }, [refreshMyOrders])

  const rows: MyOrderSummary[] = myOrders.length ? myOrders : known.map((k) => ({ order_no: k.order_no, token: k.token, status: 'new', status_label: undefined, total_bhd: k.total ?? null, created_at: new Date(k.ts).toISOString(), salesman: null, expected_delivery: null, order_kind: null }))

  return (
    <div className="px-gutter lg:px-0">
      <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.orders.title}</h1>
      <div className="mx-auto max-w-2xl lg:mx-0 lg:mt-4 lg:max-w-3xl">
        {rep && <RepCard rep={rep} compact className="mb-4" />}
        {rows.length === 0 ? (
          <EmptyState title={S.orders.empty} hint={S.orders.emptyHint} action={
              <div className="flex flex-wrap justify-center gap-2">
                <Button onClick={() => navigate('/quick')} icon={<Zap size={15} aria-hidden="true" />}>
                  {S.nav.quick}
                </Button>
                <Button variant="secondary" onClick={() => navigate('/shop')} icon={<Store size={15} aria-hidden="true" />}>
                  {S.cart.browse}
                </Button>
              </div>
            } />
        ) : (
          <>
            <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.orders.thisPhone}</h2>
            <ul className="mt-2 overflow-hidden rounded-lg border border-line bg-surface">
              {rows.map((o) => {
                const tone = o.status === 'cancelled' ? 'bad' : o.status === 'delivered' ? 'ok' : 'plum'
                return (
                  <li key={o.token} className="border-b border-line-2 last:border-b-0">
                    <Link to={`/o/${o.token}`} className="flex items-center gap-3 px-4 py-3.5 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-display text-sm font-bold tnum text-ink">{o.order_no}</span>
                          <Chip tone={tone}>{o.status_label || o.status}</Chip>
                          {o.order_kind === 'small' && <Chip tone="grey">{S.small.badge}</Chip>}
                        </div>
                        <div className="mt-0.5 text-xs text-ink-2">
                          {[fmtDate(o.created_at), o.total_bhd != null ? bhd(o.total_bhd) : null, o.salesman || null, o.expected_delivery || null].filter(Boolean).join(' · ')}
                        </div>
                      </div>
                      <ChevronRight size={18} className="shrink-0 text-ink-3" aria-hidden="true" />
                    </Link>
                  </li>
                )
              })}
            </ul>
            <p className="mt-4 text-xs leading-snug text-ink-2">{S.orders.otherPhone}</p>
          </>
        )}
      </div>
    </div>
  )
}
