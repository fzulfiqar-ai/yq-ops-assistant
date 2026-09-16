import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Clock, MessageCircle, Search, UserRound, WifiOff, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import type { Offer, QuoteProgress, RepCard, ShopItem } from '@/lib/shopApi'
import { bhd, minQtyOf, RING, stepOf, useCountdown } from '@/pages/shop/shared'
import { S } from '../strings'

/* ───────────────────────── search box ───────────────────────── */

export function SearchBox({
  value,
  onChange,
  onSubmit,
  autoFocus,
  className,
}: {
  value: string
  onChange: (v: string) => void
  onSubmit?: (v: string) => void
  autoFocus?: boolean
  className?: string
}) {
  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSubmit?.(value)
  }
  return (
    <form role="search" onSubmit={submit} className={cn('relative', className)}>
      <Search size={17} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[#a8a2bb]" aria-hidden="true" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={S.searchPlaceholder}
        aria-label={S.searchLabel}
        type="search"
        autoFocus={autoFocus}
        autoCapitalize="none"
        autoCorrect="off"
        enterKeyHint="search"
        className={cn(
          'h-12 w-full rounded-2xl border border-[#e4e0ee] bg-white pl-10 pr-11 text-[16px] text-[#1a1430] outline-none transition duration-150 ease-out placeholder:text-[#a8a2bb] hover:border-[#d9d2ee] focus:border-[#6d28d9] focus:ring-2 focus:ring-[#6d28d9]/15',
          '[&::-webkit-search-cancel-button]:hidden',
        )}
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={S.states.clear}
          className={cn('absolute right-1.5 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-[#6b6480] hover:bg-[#f4f2f9] hover:text-[#1a1430]', RING)}
        >
          <X size={16} />
        </button>
      )}
    </form>
  )
}

/* ───────────────────────── category chips ───────────────────────── */

export function CategoryChips({ categories, active, onPick }: { categories: string[]; active: string; onPick: (c: string) => void }) {
  return (
    <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="tablist" aria-label="Categories">
      {[S.categories.all, ...categories].map((c) => (
        <button
          key={c}
          role="tab"
          onClick={() => onPick(c)}
          aria-selected={active === c}
          className={cn(
            'h-9 shrink-0 rounded-full border px-3.5 text-[12.5px] font-medium capitalize transition duration-150 ease-out',
            RING,
            active === c ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'border-[#e4e0ee] bg-white text-[#4a4360] hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
          )}
        >
          {c.toLowerCase()}
        </button>
      ))}
    </div>
  )
}

/* ───────────────────────── rail ───────────────────────── */

export function Rail({ id, title, action, children }: { id: string; title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="mt-6" aria-labelledby={`rail-${id}`}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 id={`rail-${id}`} className="font-display text-[15px] font-bold tracking-[-0.01em] text-[#1a1430]">
          {title}
        </h2>
        {action}
      </div>
      <div className="-mx-4 mt-3 flex gap-3 overflow-x-auto px-4 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">{children}</div>
    </section>
  )
}

/* ───────────────────────── offer hero ───────────────────────── */

export function OfferHero({ offer, onCta }: { offer: Offer; onCta: () => void }) {
  const countdown = useCountdown(offer.ends_at)
  return (
    <section
      aria-label={offer.name}
      className="mt-4 overflow-hidden rounded-[22px] bg-[#1a1430] p-5 text-white shadow-[0_18px_40px_-18px_rgba(26,20,48,.5)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-white/60">{S.hero.label}</div>
          <h2 className="mt-1 font-display text-[20px] font-bold leading-tight tracking-[-0.02em]">{offer.name}</h2>
          {offer.summary && <p className="mt-1 text-[13px] leading-snug text-white/75">{offer.summary}</p>}
        </div>
        {offer.coupon_code && <Badge tone="ink" className="bg-white/15 ring-white/20">{offer.coupon_code}</Badge>}
      </div>
      <div className="mt-4 flex items-center justify-between gap-3">
        {countdown ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold tabular-nums text-white/80">
            <Clock size={13} aria-hidden="true" /> {S.hero.endsIn} {countdown}
          </span>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={onCta}
          className={cn('h-10 rounded-xl bg-white px-4 text-[13px] font-semibold text-[#1a1430] transition hover:bg-white/90', 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70')}
        >
          {S.hero.cta}
        </button>
      </div>
    </section>
  )
}

/* ───────────────────────── rep banner ───────────────────────── */

export function RepBanner({ rep, compact }: { rep: RepCard; compact?: boolean }) {
  const initials = (rep.name || '?')
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('')
  return (
    <div className={cn('flex items-center gap-3 rounded-[18px] border border-[#e9e2f8] bg-[#f6f2fd] px-3.5', compact ? 'py-2' : 'py-3')}>
      {rep.photo_url ? (
        <img src={rep.photo_url} alt="" width={40} height={40} className="h-10 w-10 shrink-0 rounded-full object-cover" />
      ) : (
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#6d28d9] text-[13px] font-bold text-white" aria-hidden="true">
          {initials || <UserRound size={18} />}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#6d28d9]">{S.rep.yours}</div>
        <div className="truncate font-display text-[14px] font-bold leading-tight text-[#1a1430]">{rep.name}</div>
        {!compact && rep.title && <div className="truncate text-[12px] text-[#6b6480]">{rep.title}</div>}
      </div>
      {rep.whatsapp_url && (
        <a
          href={rep.whatsapp_url}
          target="_blank"
          rel="noreferrer"
          className={cn('inline-flex h-10 shrink-0 items-center gap-1.5 rounded-xl bg-[#25D366] px-3.5 text-[12.5px] font-semibold text-white transition hover:bg-[#1eb356]', RING)}
        >
          <MessageCircle size={15} aria-hidden="true" /> {S.rep.whatsapp}
        </a>
      )}
    </div>
  )
}

/* ───────────────────────── progress bar ───────────────────────── */

export function ProgressBar({ progress }: { progress: QuoteProgress }) {
  const pct = progress.threshold_bhd
    ? Math.max(0, Math.min(100, ((Number(progress.threshold_bhd) - Number(progress.remaining_bhd || 0)) / Number(progress.threshold_bhd)) * 100))
    : 0
  const value = progress.unlocked ? 100 : pct
  return (
    <div className="rounded-[16px] border border-[#ece9f3] bg-white p-3.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-medium text-[#1a1430]">{progress.label}</span>
        {progress.unlocked && <Badge tone="green">Unlocked</Badge>}
      </div>
      <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-[#e9e5f3]" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100} aria-label={progress.label || 'Progress'}>
        <div className="h-full rounded-full bg-[#6d28d9] transition-[width] duration-500 ease-out" style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}

/* ───────────────────────── quantity keypad sheet ───────────────────────── */

export function QtySheet({ item, value, onApply, onRemove, onClose }: { item: ShopItem; value: number; onApply: (n: number) => void; onRemove: () => void; onClose: () => void }) {
  const step = stepOf(item)
  const min = minQtyOf(item)
  const [draft, setDraft] = useState(String(value || min))
  useEffect(() => setDraft(String(value || min)), [value, min])
  const presets = Array.from(new Set((step > 1 ? [1, 2, 4, 6, 10] : [6, 12, 24, 48, 100]).map((n) => (step > 1 ? n * step : n)).filter((n) => n >= min)))
  const n = Math.max(0, Math.floor(Number(draft) || 0))
  const valid = n >= min && n <= 9999
  const apply = () => {
    if (!valid) return
    onApply(n)
    onClose()
  }
  return (
    <Sheet
      open
      onClose={onClose}
      title={S.qty.title}
      subtitle={`${item.display_name || item.item_code}${min > 1 ? ` · ${S.card.min(min)}` : ''}${step > 1 ? ` · packs of ${step}` : ''}`}
      footer={
        <div className="flex gap-2">
          <button type="button" onClick={() => { onRemove(); onClose() }} className={cn('h-12 rounded-xl border border-[#f3c9d2] bg-[#fdecef] px-4 text-[13px] font-semibold text-[#9f1239]', RING)}>
            {S.qty.remove}
          </button>
          <button type="button" onClick={apply} disabled={!valid} className={cn('h-12 flex-1 rounded-xl bg-[#6d28d9] text-[14px] font-semibold text-white transition hover:bg-[#5b21b6] disabled:cursor-not-allowed disabled:bg-[#f0eef6] disabled:text-[#a8a2bb]', RING)}>
            {S.qty.apply}{valid && item.price_bhd != null ? ` · ${bhd(Number(item.price_bhd) * n)}` : ''}
          </button>
        </div>
      }
    >
      <div className="px-4 py-4 sm:px-5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').slice(0, 4))}
          onKeyDown={(e) => e.key === 'Enter' && apply()}
          inputMode="numeric"
          pattern="[0-9]*"
          autoFocus
          aria-label={S.qty.title}
          className="h-16 w-full rounded-2xl border border-[#e4e0ee] bg-white text-center font-display text-[32px] font-extrabold tabular-nums text-[#1a1430] outline-none focus:border-[#6d28d9] focus:ring-2 focus:ring-[#6d28d9]/15"
        />
        {!valid && draft !== '' && <p className="mt-1.5 text-center text-[12px] font-medium text-[#9f1239]">{n < min ? S.card.min(min) : 'Too many — please ask your representative.'}</p>}
        <div className="mt-4 text-[12px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">{S.qty.presets}</div>
        <div className="mt-2 grid grid-cols-5 gap-2">
          {presets.map((p) => (
            <button key={p} type="button" onClick={() => setDraft(String(p))} aria-pressed={n === p} className={cn('h-11 rounded-xl border text-[14px] font-semibold tabular-nums transition', RING, n === p ? 'border-[#6d28d9] bg-[#f3eefc] text-[#6d28d9]' : 'border-[#e4e0ee] bg-white text-[#1a1430] hover:bg-[#f7f5fb]')}>
              {p}
            </button>
          ))}
        </div>
        {(item.tiers || []).length > 0 && (
          <ul className="mt-4 space-y-1 text-[12px] text-[#6b6480]">
            {(item.tiers || []).map((t) => (
              <li key={t.min_qty} className={cn('flex justify-between rounded-lg px-2 py-1 tabular-nums', n >= t.min_qty && 'bg-[#f3eefc] font-semibold text-[#6d28d9]')}>
                <span>{t.min_qty}+ pcs</span>
                <span>{bhd(t.unit_price_bhd)} each</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Sheet>
  )
}

/* ───────────────────────── states ───────────────────────── */

export function CardSkeleton({ compact }: { compact?: boolean }) {
  return (
    <div className={cn('overflow-hidden rounded-[20px] border border-[#ece9f3] bg-white', compact && 'w-[10.5rem] shrink-0 sm:w-[12rem]')}>
      <div className="aspect-square w-full animate-pulse bg-[#f4f2f9]" />
      <div className="space-y-2 border-t border-[#f4f2f9] p-3">
        <div className="h-3 w-3/5 animate-pulse rounded bg-[#f0eef6]" />
        <div className="h-2.5 w-full animate-pulse rounded bg-[#f4f2f9]" />
        <div className="h-5 w-20 animate-pulse rounded-full bg-[#f4f2f9]" />
        <div className="h-4 w-24 animate-pulse rounded bg-[#f0eef6]" />
        <div className="h-11 w-full animate-pulse rounded-xl bg-[#f4f2f9]" />
      </div>
    </div>
  )
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="rounded-[20px] border border-[#ece9f3] bg-white px-6 py-14 text-center">
      <p className="font-display text-[15px] font-bold text-[#1a1430]">{title}</p>
      {hint && <p className="mt-1 text-[12.5px] leading-snug text-[#6b6480]">{hint}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  )
}

export function OfflineBanner({ kind, onRetry }: { kind: 'offline' | 'loading-slow'; onRetry?: () => void }) {
  return (
    <div className="mt-3 flex items-center gap-2 rounded-xl bg-[#fdf3e3] px-3 py-2 text-[12px] leading-snug text-[#96600d]">
      <WifiOff size={14} className="shrink-0" aria-hidden="true" />
      <span className="flex-1">{kind === 'offline' ? S.states.offline : S.states.connectingHint}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="shrink-0 font-semibold underline underline-offset-2">
          {S.states.retry}
        </button>
      )}
    </div>
  )
}

export function ConnectingState({ onRetry, failed }: { onRetry: () => void; failed: boolean }) {
  return (
    <div className="mx-auto max-w-sm px-4 py-16 text-center">
      <div className={cn('mx-auto h-10 w-10 rounded-full border-4 border-[#e9e5f3] border-t-[#6d28d9]', !failed && 'animate-spin')} aria-hidden="true" />
      <h2 className="mt-5 font-display text-[17px] font-bold text-[#1a1430]">{failed ? 'We could not reach YQ' : S.states.connecting}</h2>
      <p className="mt-1 text-[12.5px] leading-snug text-[#6b6480]">{failed ? 'Check your connection and try again.' : S.states.connectingHint}</p>
      {failed && (
        <button type="button" onClick={onRetry} className={cn('mt-5 h-11 rounded-xl bg-[#6d28d9] px-5 text-[13px] font-semibold text-white', RING)}>
          {S.states.retry}
        </button>
      )}
    </div>
  )
}

export function ClosedState() {
  return (
    <div className="mx-auto max-w-sm px-4 py-16 text-center">
      <h2 className="font-display text-[17px] font-bold text-[#1a1430]">{S.states.closed}</h2>
      <p className="mt-1 text-[12.5px] text-[#6b6480]">{S.states.closedHint}</p>
      <Link to="/orders" className={cn('mt-5 inline-flex h-11 items-center rounded-xl border border-[#e4e0ee] bg-white px-5 text-[13px] font-semibold text-[#1a1430]', RING)}>
        {S.orders.title}
      </Link>
    </div>
  )
}

export const BTN_SECONDARY = cn(
  'inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-[#e4e0ee] bg-white px-4 text-[13px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
  RING,
)
export const BTN_PRIMARY = cn(
  'inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-[#6d28d9] px-5 text-[14px] font-semibold text-white transition duration-150 ease-out hover:bg-[#5b21b6] active:scale-[.995] disabled:cursor-not-allowed disabled:bg-[#f0eef6] disabled:text-[#a8a2bb]',
  RING,
)
