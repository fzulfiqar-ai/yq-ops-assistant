import { useState, type ReactNode } from 'react'
import { Eye, EyeOff, Hourglass, ImageOff, Loader2, Radar } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { PHOTO_ROLE_LABEL, type MiPhoto } from '@/lib/marketIntel'

/** The pieces every Market Intel tab and the case page share (moved out of pages/MarketIntel.tsx in r8m). */

/** v1: the R7b tables are not there yet (the board and My reports answer available: false). */
export function NotReady({ hint }: { hint?: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card px-5 py-10 text-center">
      <Radar size={24} className="mx-auto text-primary" aria-hidden="true" />
      <p className="mx-auto mt-3 max-w-md text-[13.5px] text-muted-foreground">
        {hint || 'Market Intel is not set up yet.'} Sightings saved meanwhile are kept on the Finds board and copied in afterwards.
      </p>
    </div>
  )
}

/**
 * v2: a route of this release answered 503 (its tables are not migrated yet) or does not exist on
 * this API yet. A calm panel, never an error: the rest of the page keeps working.
 */
export function NotAvailableYet({ what }: { what: string }) {
  return (
    <div role="status" className="rounded-2xl border border-dashed border-border bg-card px-5 py-10 text-center">
      <span className="mx-auto grid h-10 w-10 place-items-center rounded-xl bg-accent text-accent-foreground">
        <Hourglass size={18} aria-hidden="true" />
      </span>
      <p className="mt-3 font-display text-[15px] font-bold">Not available yet</p>
      <p className="mx-auto mt-1 max-w-md text-[13px] leading-snug text-muted-foreground">
        {what} switch on after the next database update. Everything else on this page works as before.
      </p>
    </div>
  )
}

export function Empty({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card px-5 py-10 text-center text-[13.5px] text-muted-foreground">
      <span className="mx-auto mb-2 grid h-10 w-10 place-items-center rounded-xl bg-accent text-accent-foreground">{icon}</span>
      {children}
    </div>
  )
}

export function Loading({ className }: { className?: string }) {
  return (
    <div className={cn('grid h-40 place-items-center text-muted-foreground', className)}>
      <Loader2 className="animate-spin" size={20} aria-label="Loading" />
    </div>
  )
}

export function Cover({ url, alt, className }: { url?: string | null; alt: string; className?: string }) {
  return url ? (
    <img src={url} alt={alt} loading="lazy" decoding="async" className={cn('h-full w-full object-cover', className)} />
  ) : (
    <span className={cn('grid h-full w-full place-items-center bg-gradient-to-br from-accent/50 to-accent text-sash', className)}>
      <ImageOff size={22} aria-hidden="true" />
    </span>
  )
}

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  size = 'md',
  className,
}: {
  value: T
  onChange: (v: T) => void
  tabs: { value: T; label: string; count?: number | null; icon?: ReactNode; urgent?: boolean }[]
  size?: 'md' | 'sm'
  className?: string
}) {
  return (
    <div role="tablist" className={cn('mb-5 flex gap-1 overflow-x-auto rounded-2xl border border-border bg-muted p-1', size === 'sm' && 'mb-4 rounded-xl', className)}>
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            'flex shrink-0 items-center gap-2 rounded-xl px-3.5 font-semibold transition-colors',
            size === 'sm' ? 'h-9 text-[12.5px]' : 'h-10 text-[13px]',
            value === t.value ? 'bg-card text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {t.icon}
          {t.label}
          {t.count ? (
            <span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', t.urgent ? 'bg-destructive text-destructive-foreground' : 'bg-primary/10 text-primary')}>{t.count}</span>
          ) : null}
        </button>
      ))}
    </div>
  )
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</div>
      <div className="truncate text-[13.5px] font-semibold text-foreground">{children}</div>
    </div>
  )
}

/** Previous · "49–96 of 230" · Next, for a list the API pages (offset / limit, true count). */
export function Pager({ offset, limit, count, onPage }: { offset: number; limit: number; count: number; onPage: (offset: number) => void }) {
  if (count <= limit && offset === 0) return null
  return (
    <div className="mt-4 flex items-center justify-center gap-2">
      <button type="button" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))} className="h-10 rounded-xl border border-border px-4 text-[13px] font-semibold disabled:opacity-40">
        Previous
      </button>
      <span className="text-[12.5px] tabular-nums text-muted-foreground">
        {count ? offset + 1 : 0}–{Math.min(count, offset + limit)} of {count}
      </span>
      <button type="button" disabled={offset + limit >= count} onClick={() => onPage(offset + limit)} className="h-10 rounded-xl border border-border px-4 text-[13px] font-semibold disabled:opacity-40">
        Next
      </button>
    </div>
  )
}

/**
 * One evidence photo: the 320 px thumbnail, opening the signed master. A photo flagged with people
 * comes from the server already blurred (its own derivative; an admin may ask for the original on
 * the case page) — the CSS blur stays as a second guard for an API that predates the derivative.
 */
export function PhotoTile({
  p,
  alt,
  raw = false,
  canFlag = false,
  onFlag,
  className,
}: {
  p: MiPhoto
  alt: string
  raw?: boolean
  canFlag?: boolean
  onFlag?: (flag: boolean) => void
  className?: string
}) {
  const people = Boolean(p.has_people || p.has_people_flag)
  const src = p.thumb_url || p.url
  const [flagging, setFlagging] = useState(false)
  return (
    <figure className={cn('relative h-28 w-28 shrink-0 overflow-hidden rounded-xl bg-muted', className)}>
      {p.url ? (
        <a href={p.url} target="_blank" rel="noreferrer" className={cn('block h-full w-full', people && !raw && 'blur-md')}>
          <Cover url={src} alt={alt} />
        </a>
      ) : (
        <span className={cn('block h-full w-full', people && !raw && 'blur-md')}>
          <Cover url={src} alt={alt} />
        </span>
      )}
      <span className="pointer-events-none absolute left-1.5 right-1.5 top-1.5 flex flex-wrap gap-1">
        {p.role && p.role !== 'product' && <Badge tone="ink">{PHOTO_ROLE_LABEL[p.role] || p.role}</Badge>}
        {people && <Badge tone="rose">People</Badge>}
      </span>
      {canFlag && onFlag && (
        <button
          type="button"
          disabled={flagging}
          onClick={() => {
            setFlagging(true)
            onFlag(!people)
            window.setTimeout(() => setFlagging(false), 1200)
          }}
          aria-label={people ? 'Remove the people flag' : 'Flag people in this photo'}
          title={people ? 'Remove the people flag' : 'Flag people in this photo (it is blurred and kept out of reports)'}
          className="absolute bottom-1.5 right-1.5 inline-flex h-8 items-center gap-1 rounded-lg bg-black/60 px-1.5 text-[10.5px] font-semibold text-white hover:bg-black/75 disabled:opacity-60"
        >
          {people ? <Eye size={12} aria-hidden="true" /> : <EyeOff size={12} aria-hidden="true" />}
          {people ? 'Unflag' : 'Flag people'}
        </button>
      )}
    </figure>
  )
}
