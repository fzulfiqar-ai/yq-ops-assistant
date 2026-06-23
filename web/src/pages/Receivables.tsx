import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { bhd, num, fmtDate } from '@/lib/format'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Skeleton } from '@/components/ui/skeleton'

interface Row {
  account: string
  outstanding_bhd: number
  days_outstanding: number
  salesman: string
  last_entry_date: string
}
interface Data {
  rows: Row[]
  total: number
  count: number
  overdue_count: number
}

const cols: Column<Row>[] = [
  { key: 'account', label: 'Account' },
  { key: 'salesman', label: 'Salesman' },
  { key: 'last_entry_date', label: 'Last entry', align: 'right', render: (v) => fmtDate(v as string) },
  {
    key: 'days_outstanding',
    label: 'Days',
    align: 'right',
    render: (v) => {
      const n = Number(v ?? 0)
      return <span className={cn('font-semibold', n >= 90 ? 'text-rose-600' : n >= 30 ? 'text-amber-600' : '')}>{num(n)}</span>
    },
  },
  { key: 'outstanding_bhd', label: 'Outstanding', align: 'right', money: true },
]

export default function Receivables() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'receivables'], queryFn: () => apiGet<Data>('/report/receivables') })
  return (
    <div>
      <PageHeader title="Receivables" subtitle="Outstanding balances and ageing" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-3 gap-3 sm:max-w-xl">
            <Stat label="Total outstanding" value={bhd(data.total, 0)} tone="violet" />
            <Stat label="Accounts" value={num(data.count)} />
            <Stat label="Overdue 30+ days" value={num(data.overdue_count)} tone="amber" />
          </div>
          <DataTable
            rows={data.rows}
            cols={cols}
            rowClass={(r) => (Number(r.days_outstanding) >= 90 ? 'bg-rose-50/60 dark:bg-rose-500/5' : Number(r.days_outstanding) >= 30 ? 'bg-amber-50/50 dark:bg-amber-500/5' : undefined)}
            empty="No receivables."
          />
        </>
      )}
    </div>
  )
}
