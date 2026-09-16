import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { bhd, fmtDate, RING } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { BTN_SECONDARY, EmptyState, RepBanner } from '../components/Bits'
import { BottomNav, Page, TopBar } from '../components/Chrome'
import { rememberedOrders } from '../lib/device'
import { S } from '../strings'

/** /orders — the orders this phone placed (tokens are the capability; nothing else is listed). */
export default function MyOrdersPage() {
  const navigate = useNavigate()
  const { myOrders, refreshMyOrders, rep } = useMarket()
  const known = rememberedOrders()

  useEffect(() => {
    document.title = `${S.orders.title} · ${S.brand}`
    refreshMyOrders()
  }, [refreshMyOrders])

  const rows = myOrders.length ? myOrders : known.map((k) => ({ order_no: k.order_no, token: k.token, status: 'new', status_label: undefined, total_bhd: k.total ?? null, created_at: new Date(k.ts).toISOString() }))

  return (
    <Page>
      <TopBar title={S.orders.title} />
      <main className="mx-auto max-w-2xl px-4 pt-2">
        {rep && <div className="mb-4"><RepBanner rep={rep} compact /></div>}
        {rows.length === 0 ? (
          <EmptyState title={S.orders.empty} hint={S.orders.emptyHint} action={<button type="button" onClick={() => navigate('/')} className={BTN_SECONDARY}>{S.cart.browse}</button>} />
        ) : (
          <>
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">{S.orders.thisPhone}</h2>
            <ul className="mt-2 overflow-hidden rounded-[20px] border border-[#ece9f3] bg-white">
              {rows.map((o) => {
                const tone = o.status === 'cancelled' ? 'rose' : o.status === 'delivered' ? 'green' : 'accent'
                return (
                  <li key={o.token} className="border-b border-[#f4f2f9] last:border-b-0">
                    <Link to={`/o/${o.token}`} className={cn('flex items-center gap-3 px-4 py-3.5 hover:bg-[#faf9fc]', RING)}>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-display text-[14px] font-bold tabular-nums text-[#1a1430]">{o.order_no}</span>
                          <Badge tone={tone}>{o.status_label || o.status}</Badge>
                        </div>
                        <div className="mt-0.5 text-[11.5px] text-[#6b6480]">
                          {fmtDate(o.created_at)}{o.total_bhd != null ? ` · ${bhd(o.total_bhd)}` : ''}{'salesman' in o && o.salesman ? ` · ${o.salesman}` : ''}{'expected_delivery' in o && o.expected_delivery ? ` · ${o.expected_delivery}` : ''}
                        </div>
                      </div>
                      <ChevronRight size={18} className="shrink-0 text-[#a8a2bb]" aria-hidden="true" />
                    </Link>
                  </li>
                )
              })}
            </ul>
            <p className="mt-4 text-[11.5px] leading-snug text-[#6b6480]">{S.orders.otherPhone}</p>
          </>
        )}
      </main>
      <BottomNav />
    </Page>
  )
}
