import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, MessageSquareQuote, Landmark, Package, Combine, Loader2, ArrowLeft, NotebookPen } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { bhd } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'

interface Account { customer_name: string; total_revenue_bhd: number; order_count: number; last_order_date: string | null }
interface Brief {
  account: string
  profile: { orders?: number; revenue_bhd?: number; last_order?: string | null; first_order?: string | null }
  top_items: { item_name: string; qty: number; revenue_bhd: number }[]
  cross_sell: { suggest: string; together: number }[]
  open_ar: { outstanding_bhd?: number; overdue_bhd?: number }
  field_notes: string
  talking_points: string[]
  summary: string
}

export default function Coaching() {
  const [q, setQ] = useState('')
  const [account, setAccount] = useState<string | null>(null)
  const { data: accounts } = useQuery({ queryKey: ['coach-accounts'], queryFn: () => apiGet<Account[]>('/coaching/accounts') })
  const { data: brief, isFetching } = useQuery({
    queryKey: ['coach-brief', account],
    queryFn: () => apiGet<Brief>(`/coaching/brief?account=${encodeURIComponent(account || '')}`),
    enabled: !!account,
  })

  const matches = (accounts || []).filter((a) => a.customer_name.toLowerCase().includes(q.toLowerCase())).slice(0, 30)

  if (account) {
    const p = brief?.profile || {}
    return (
      <div>
        <PageHeader title="Pre-visit coach" subtitle="Everything you need before you walk in — what they buy, owe, and what to pitch next" />
        <button onClick={() => setAccount(null)} className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition hover:text-foreground">
          <ArrowLeft size={15} /> Pick another account
        </button>

        {isFetching && !brief ? (
          <Card className="flex items-center justify-center p-12"><Loader2 className="animate-spin text-primary" /></Card>
        ) : (
          <div className="space-y-4">
            {/* Profile */}
            <Card className="p-5">
              <div className="font-display text-lg font-bold">{account}</div>
              <div className="mt-3 grid grid-cols-3 gap-3 text-center">
                <div><div className="font-display text-xl font-extrabold tabular-nums">{bhd(Number(p.revenue_bhd) || 0, 0)}</div><div className="text-[11px] text-muted-foreground">lifetime</div></div>
                <div><div className="font-display text-xl font-extrabold tabular-nums">{p.orders ?? 0}</div><div className="text-[11px] text-muted-foreground">orders</div></div>
                <div><div className="font-display text-sm font-bold">{p.last_order || '—'}</div><div className="text-[11px] text-muted-foreground">last order</div></div>
              </div>
              {!!brief?.open_ar?.outstanding_bhd && (
                <div className="mt-3 flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                  <Landmark size={15} /> Open balance {bhd(Number(brief.open_ar.outstanding_bhd), 3)}{Number(brief.open_ar.overdue_bhd) > 0 ? ` · ${bhd(Number(brief.open_ar.overdue_bhd), 3)} overdue` : ''} — collect tactfully.
                </div>
              )}
            </Card>

            {/* Talking points */}
            {!!brief?.talking_points?.length && (
              <Card className="p-5">
                <div className="mb-2 flex items-center gap-2 font-display text-base font-semibold"><MessageSquareQuote size={18} className="text-primary" /> Talking points</div>
                <ul className="space-y-1.5">
                  {brief.talking_points.map((t, i) => (
                    <li key={i} className="flex gap-2 text-sm"><span className="text-primary">•</span><span>{t}</span></li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Cross-sell */}
            {!!brief?.cross_sell?.length && (
              <Card className="p-5">
                <div className="mb-2 flex items-center gap-2 font-display text-base font-semibold"><Combine size={18} className="text-primary" /> Pitch next (cross-sell)</div>
                <div className="flex flex-wrap gap-2">
                  {brief.cross_sell.map((c, i) => (
                    <span key={i} className="rounded-lg border border-border bg-accent/40 px-2.5 py-1.5 text-[13px] font-medium">{c.suggest} <span className="text-muted-foreground">· {c.together}×</span></span>
                  ))}
                </div>
              </Card>
            )}

            {/* Top items */}
            {!!brief?.top_items?.length && (
              <Card className="p-5">
                <div className="mb-2 flex items-center gap-2 font-display text-base font-semibold"><Package size={18} className="text-primary" /> Their staples (reorder these)</div>
                <ul className="space-y-1">
                  {brief.top_items.slice(0, 6).map((t, i) => (
                    <li key={i} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-accent/40">
                      <span className="truncate font-medium">{t.item_name}</span>
                      <span className="shrink-0 text-muted-foreground tabular-nums">{Number(t.qty).toLocaleString()} · {bhd(Number(t.revenue_bhd) || 0, 0)}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Field notes */}
            {brief?.field_notes && (
              <Card className="p-5">
                <div className="mb-2 flex items-center gap-2 font-display text-base font-semibold"><NotebookPen size={18} className="text-primary" /> From the field</div>
                <p className="whitespace-pre-line text-sm text-muted-foreground">{brief.field_notes}</p>
              </Card>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Pre-visit coach" subtitle="Pick an account to get a ready brief before you call or visit" />
      <div className="mb-4 flex items-center gap-2 rounded-2xl border bg-card px-3.5 py-2.5 shadow-soft focus-within:border-primary/40">
        <Search size={17} className="text-muted-foreground" />
        <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus placeholder="Search an account…"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {matches.map((a) => (
          <button key={a.customer_name} onClick={() => setAccount(a.customer_name)}
            className="flex items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 text-left transition hover:border-primary/40 hover:bg-accent/40">
            <div className="min-w-0">
              <div className="truncate font-medium">{a.customer_name}</div>
              <div className="text-[11px] text-muted-foreground">{a.order_count} orders · last {a.last_order_date || '—'}</div>
            </div>
            <span className="shrink-0 text-sm font-semibold text-primary">{bhd(Number(a.total_revenue_bhd) || 0, 0)}</span>
          </button>
        ))}
        {!matches.length && <Card className="col-span-full p-8 text-center text-sm text-muted-foreground">No matching accounts.</Card>}
      </div>
    </div>
  )
}
