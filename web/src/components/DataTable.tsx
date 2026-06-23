import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'

export interface Column<T> {
  key: keyof T & string
  label: string
  money?: boolean
  align?: 'left' | 'right'
  render?: (value: unknown, row: T) => ReactNode
}

export function DataTable<T extends object>({
  rows,
  cols,
  rowClass,
  empty = 'No data.',
  maxHeight = '62vh',
}: {
  rows: T[]
  cols: Column<T>[]
  rowClass?: (row: T) => string | undefined
  empty?: string
  maxHeight?: string
}) {
  if (!rows.length) {
    return <div className="py-12 text-center text-sm text-muted-foreground">{empty}</div>
  }
  return (
    <div className="overflow-auto rounded-xl border" style={{ maxHeight }}>
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-secondary/80 backdrop-blur">
          <tr>
            {cols.map((c) => (
              <th
                key={c.key}
                className={cn(
                  'whitespace-nowrap px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground',
                  c.align === 'right' ? 'text-right' : 'text-left',
                )}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={cn('border-t transition-colors hover:bg-accent/40', rowClass?.(row))}>
              {cols.map((c) => {
                const v = (row as Record<string, unknown>)[c.key]
                return (
                  <td
                    key={c.key}
                    className={cn(
                      'whitespace-nowrap px-4 py-2.5',
                      c.align === 'right' ? 'text-right tabular-nums' : 'text-left',
                    )}
                  >
                    {c.render ? c.render(v, row) : c.money ? bhd(Number(v ?? 0)) : v == null ? '—' : String(v)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: 'amber' | 'rose' | 'violet' }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3 shadow-soft">
      <div
        className={cn(
          'font-display text-xl font-extrabold tracking-tight',
          tone === 'amber' && 'text-amber-600',
          tone === 'rose' && 'text-rose-600',
          tone === 'violet' && 'text-primary',
        )}
      >
        {value}
      </div>
      <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  )
}
