import { useState } from 'react'
import { FlaskConical, ArrowRight, Loader2, AlertCircle } from 'lucide-react'
import { apiGet, ApiError } from '@/lib/api'
import { bhd, num } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

interface SimResult {
  ok: boolean
  code?: string
  reason?: string
  confidence?: 'low' | 'medium' | 'high'
  elasticity?: number
  price_epochs_used?: number
  current_price_bhd?: number
  new_price_bhd?: number
  price_change_pct?: number
  baseline_monthly_qty?: number
  projected_monthly_qty?: number
  current_monthly_revenue_bhd?: number
  projected_monthly_revenue_bhd?: number
  revenue_delta_bhd?: number
  landed_cost_bhd?: number
  current_monthly_margin_bhd?: number
  projected_monthly_margin_bhd?: number
  margin_delta_bhd?: number
  margin_note?: string
  summary?: string
}

const CONF: Record<string, string> = {
  high: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300',
  medium: 'bg-amber-500/15 text-amber-600 dark:text-amber-300',
  low: 'bg-slate-500/15 text-slate-600 dark:text-slate-300',
}

function Compare({ label, before, after, delta, money }: {
  label: string; before: number; after: number; delta: number; money?: boolean
}) {
  const fmt = (n: number) => (money ? bhd(n, 0) : num(Math.round(n)))
  const good = delta >= 0
  return (
    <div className="rounded-lg border bg-card px-3 py-2.5">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-center gap-1.5 text-sm">
        <span className="text-muted-foreground">{fmt(before)}</span>
        <ArrowRight className="h-3 w-3 text-muted-foreground" />
        <span className="font-semibold tabular-nums">{fmt(after)}</span>
      </div>
      <div className={cn('mt-0.5 text-xs font-medium tabular-nums', good ? 'text-emerald-600' : 'text-rose-600')}>
        {good ? '+' : ''}{fmt(delta)}
      </div>
    </div>
  )
}

export function PriceSimulator() {
  const [item, setItem] = useState('')
  const [price, setPrice] = useState('')
  const [res, setRes] = useState<SimResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function run() {
    if (!item.trim() || !price) return
    setBusy(true); setErr(''); setRes(null)
    try {
      const r = await apiGet<SimResult>(`/bi/price-simulator?item=${encodeURIComponent(item.trim())}&new_price=${encodeURIComponent(price)}`)
      setRes(r)
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 403 ? 'You need Margins access to run this.' : 'Simulation failed — try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-primary" />
        <h2 className="font-display text-lg font-semibold">What-if price simulator</h2>
      </div>
      <p className="mb-3 text-sm text-muted-foreground">
        Project the impact of a price change from real price history. Enter an item code and a new price.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Item / code</label>
          <input value={item} onChange={(e) => setItem(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && run()}
            placeholder="e.g. X05" className="w-40 rounded-lg border bg-background px-3 py-1.5 text-sm" />
        </div>
        <div>
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">New price (BHD)</label>
          <input value={price} onChange={(e) => setPrice(e.target.value)} type="number" step="0.001" min="0"
            onKeyDown={(e) => e.key === 'Enter' && run()}
            placeholder="e.g. 1.500" className="w-36 rounded-lg border bg-background px-3 py-1.5 text-sm" />
        </div>
        <button onClick={run} disabled={busy || !item.trim() || !price}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
          Simulate
        </button>
      </div>

      {err && <div className="mt-3 flex items-center gap-1.5 text-sm text-rose-600"><AlertCircle className="h-4 w-4" />{err}</div>}

      {res && !res.ok && (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.05] p-3 text-sm">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <span className="text-muted-foreground">{res.reason}</span>
        </div>
      )}

      {res && res.ok && (
        <div className="mt-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">{res.code}</span>
            <span className="text-xs text-muted-foreground">
              BHD {res.current_price_bhd?.toFixed(3)} → {res.new_price_bhd?.toFixed(3)} ({res.price_change_pct}%)
            </span>
            <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-semibold', CONF[res.confidence || 'low'])}>
              {res.confidence} confidence · elasticity {res.elasticity} · {res.price_epochs_used} price move(s)
            </span>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <Compare label="Monthly qty" before={res.baseline_monthly_qty || 0} after={res.projected_monthly_qty || 0}
              delta={(res.projected_monthly_qty || 0) - (res.baseline_monthly_qty || 0)} />
            <Compare label="Monthly revenue" money before={res.current_monthly_revenue_bhd || 0}
              after={res.projected_monthly_revenue_bhd || 0} delta={res.revenue_delta_bhd || 0} />
            {res.margin_delta_bhd !== undefined ? (
              <Compare label="Monthly margin" money before={res.current_monthly_margin_bhd || 0}
                after={res.projected_monthly_margin_bhd || 0} delta={res.margin_delta_bhd || 0} />
            ) : (
              <div className="rounded-lg border bg-card px-3 py-2.5 text-xs text-muted-foreground">{res.margin_note}</div>
            )}
          </div>
          <p className="mt-3 text-sm text-muted-foreground">{res.summary}</p>
        </div>
      )}
    </Card>
  )
}
