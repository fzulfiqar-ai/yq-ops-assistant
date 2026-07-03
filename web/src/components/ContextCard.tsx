import { ArrowUpRight, ArrowDownRight, Table2, Gauge } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ContextCardData {
  kind: 'kpi' | 'table' | 'entity' | 'item'
  agent: string
  title: string
  summary?: string
  metrics?: Record<string, number>
  rows?: Record<string, unknown>[]
  delta?: { metric: string; value: number } | null
  image_url?: string | null
  package_image_url?: string | null
}

/** Decode the base64-JSON ⟦card:…⟧ marker into card data (never throws). */
export function parseCards(b64: string): ContextCardData[] {
  try {
    const json = decodeURIComponent(escape(atob(b64)))
    const arr = JSON.parse(json)
    return Array.isArray(arr) ? arr : []
  } catch {
    return []
  }
}

const prettyKey = (k: string) => k.replace(/_bhd$/i, '').replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
function fmtNum(k: string, v: unknown) {
  if (typeof v !== 'number') return v == null ? '—' : String(v)
  return /bhd/i.test(k) ? `BHD ${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : v.toLocaleString('en-US')
}

function DeltaBadge({ metric, value }: { metric: string; value: number }) {
  const up = value > 0
  return (
    <span className={cn('inline-flex items-center gap-0.5 text-[11px] font-medium',
      up ? 'text-rose-500' : 'text-emerald-500')}>
      {up ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
      {fmtNum(metric, Math.abs(value))} {prettyKey(metric)}
    </span>
  )
}

function OneCard({ card }: { card: ContextCardData }) {
  const metrics = Object.entries(card.metrics || {}).slice(0, 4)
  const rows = card.rows || []
  const cols = rows.length ? Object.keys(rows[0]).slice(0, 4) : []
  return (
    <div className="rounded-lg border border-violet-500/20 bg-violet-500/[0.03] p-3">
      <div className="mb-2 flex items-center gap-1.5">
        {card.kind === 'table' ? <Table2 className="h-3.5 w-3.5 text-violet-500" />
          : <Gauge className="h-3.5 w-3.5 text-violet-500" />}
        <span className="text-xs font-semibold">{card.title}</span>
        {card.delta && <span className="ml-auto"><DeltaBadge {...card.delta} /></span>}
      </div>

      {card.kind === 'item' && card.image_url && (
        <a href={card.image_url} target="_blank" rel="noreferrer"
          className="mb-2 block rounded-md bg-white p-2">
          <img src={card.image_url} alt={card.title} loading="lazy"
            className="mx-auto max-h-44 object-contain" />
        </a>
      )}
      {card.kind === 'item' && card.summary && (
        <p className="mb-2 text-[12px] leading-snug text-muted-foreground">{card.summary}</p>
      )}

      {metrics.length > 0 && (
        <div className="mb-2 grid grid-cols-2 gap-2">
          {metrics.map(([k, v]) => (
            <div key={k} className="rounded-md bg-background/60 px-2 py-1.5">
              <div className="text-sm font-semibold tabular-nums">{fmtNum(k, v)}</div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{prettyKey(k)}</div>
            </div>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-muted-foreground">
                {cols.map((c) => <th key={c} className="pb-1 pr-2 font-medium">{prettyKey(c)}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-border/40">
                  {cols.map((c) => <td key={c} className="py-1 pr-2 tabular-nums">{fmtNum(c, r[c])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function ContextCards({ cards }: { cards: ContextCardData[] }) {
  if (!cards?.length) return null
  return (
    <div className="mb-2 grid gap-2 sm:grid-cols-2">
      {cards.map((c, i) => <OneCard key={i} card={c} />)}
    </div>
  )
}
