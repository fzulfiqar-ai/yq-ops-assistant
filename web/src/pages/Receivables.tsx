import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { apiGet } from '@/lib/api'
import { bhd, fmtDate, num } from '@/lib/format'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface Row {
  account: string
  group_name: string
  outstanding_bhd: number
  overdue_bhd: number
  over_90_bhd: number
}
interface Data {
  rows: Row[]
  total: number
  over_90: number
  count: number
  overdue_count: number
  buckets: Record<string, number>
  /** Focus's own Grand Total for this snapshot (R2). null until the ageing totals are stored. */
  focus_total?: number | null
  focus_over_90?: number | null
  focus_as_of?: string | null
  /** row sum minus Focus total: positive = the rows show more than Focus's own book */
  focus_gap?: number | null
}

const BUCKET_LABELS: [string, string][] = [
  ['b_0_30', '0-30'], ['b_31_60', '31-60'], ['b_61_90', '61-90'], ['b_91_120', '91-120'],
  ['b_121_150', '121-150'], ['b_151_180', '151-180'], ['b_181_210', '181-210'], ['b_over_210', '>210'],
]

const cols: Column<Row>[] = [
  { key: 'account', label: 'Account' },
  { key: 'group_name', label: 'Group', render: (v) => v ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-medium text-accent-foreground">{String(v)}</span> : '—' },
  { key: 'outstanding_bhd', label: 'Outstanding', align: 'right', money: true },
  { key: 'overdue_bhd', label: 'Overdue', align: 'right', render: (v) => (Number(v) > 0 ? <span className="font-semibold text-amber-600">{bhd(Number(v), 0)}</span> : '—') },
  { key: 'over_90_bhd', label: '> 90 days', align: 'right', render: (v) => (Number(v) > 0 ? <span className="font-semibold text-rose-600">{bhd(Number(v), 0)}</span> : '—') },
]

export default function Receivables() {
  const [params] = useSearchParams()
  const { data, isLoading } = useQuery({ queryKey: ['report', 'receivables'], queryFn: () => apiGet<Data>('/report/receivables') })
  const bucketData = data ? BUCKET_LABELS.map(([k, label]) => ({ label, value: Number(data.buckets[k] || 0) })) : []
  const hasGap = data?.focus_total != null && data.focus_gap != null && Math.abs(Number(data.focus_gap)) > 0.005
  return (
    <div>
      <PageHeader title="Receivables" subtitle="Cash & trade-debtor balances with ageing (Focus AR)" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className={cn('mb-4 grid grid-cols-2 gap-3', data.focus_total != null ? 'lg:grid-cols-5' : 'lg:grid-cols-4')}>
            <Stat label="Total receivable (rows)" value={bhd(data.total, 0)} tone="violet"
              foot={data.focus_total != null ? 'sum of the accounts below' : undefined} />
            {data.focus_total != null && (
              <Stat label="Focus total" value={bhd(data.focus_total, 0)} tone={hasGap ? 'amber' : 'slate'}
                foot={hasGap
                  ? `Rows are ${bhd(Math.abs(Number(data.focus_gap)), 2)} ${Number(data.focus_gap) > 0 ? 'above' : 'below'} Focus's own Grand Total`
                  : "matches Focus's own Grand Total"} />
            )}
            <Stat label="Over 90 days" value={bhd(data.over_90, 0)} tone="rose"
              foot={data.focus_over_90 != null && Math.abs(Number(data.focus_over_90) - data.over_90) > 0.005
                ? `Focus: ${bhd(data.focus_over_90, 0)}` : undefined} />
            <Stat label="Debtor accounts" value={num(data.count)} />
            <Stat label="With overdue" value={num(data.overdue_count)} tone="amber" />
          </div>

          {hasGap && (
            <Card className="mb-4 border-amber-300/70 bg-amber-50/60 p-4 text-[13px] leading-relaxed dark:border-amber-500/30 dark:bg-amber-500/5">
              <b>Credits may be shown as owed.</b> The ageing export lists every balance as a positive number, so a
              customer credit (money YQ owes the shop) is counted here as money owed to YQ. The accounts below add up to{' '}
              <b>{bhd(data.total, 2)}</b>; Focus's own Grand Total for the same snapshot
              {data.focus_as_of ? ` (${fmtDate(data.focus_as_of)})` : ''} is <b>{bhd(Number(data.focus_total), 2)}</b> — a gap of{' '}
              <b>{bhd(Number(data.focus_gap), 2)}</b>. No sign is guessed: ask accounts for a signed or Dr/Cr ageing export to settle which accounts are credits.
            </Card>
          )}

          <Card className="mb-4 p-5">
            <div className="mb-3 font-display text-base font-semibold">Ageing buckets (days past due)</div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={bucketData} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="label" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false}
                  width={44} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`)} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Outstanding']} cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                  {bucketData.map((_, i) => <Cell key={i} fill={i < 1 ? '#10b981' : i < 3 ? '#f59e0b' : '#f43f5e'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>

          <DataTable
            rows={data.rows}
            cols={cols}
            exportName="receivables"
            initialQuery={params.get('q') || ''}
            rowClass={(r) => (Number(r.over_90_bhd) > 0 ? 'bg-rose-50/60 dark:bg-rose-500/5' : Number(r.overdue_bhd) > 0 ? 'bg-amber-50/50 dark:bg-amber-500/5' : undefined)}
            empty="No receivables."
          />
        </>
      )}
    </div>
  )
}
