import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

/** Shared components of the portal's YQ Assist page (web/src/pages/MerchantVoice.tsx and its tabs). */

export function NotReady({ what }: { what: string }) {
  return (
    <Card className="p-6 text-sm text-muted-foreground">
      {what} is not switched on yet. It appears here once the YQ Assist database update has been applied and the
      first merchants have used it.
    </Card>
  )
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; count?: number | null }[] }) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1.5">
      {items.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cn('inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-semibold transition',
            value === t.id ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}>
          {t.label}
          {t.count ? <span className={cn('rounded-full px-1.5 text-[11px]', value === t.id ? 'bg-white/20' : 'bg-rose-100 text-rose-700')}>{t.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function Pills<T extends string | number>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string }[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((t) => (
        <button key={String(t.id)} type="button" aria-pressed={value === t.id} onClick={() => onChange(t.id)}
          className={cn('rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset transition',
            value === t.id ? 'bg-primary/10 text-primary ring-primary/30' : 'text-muted-foreground ring-border hover:text-foreground')}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

