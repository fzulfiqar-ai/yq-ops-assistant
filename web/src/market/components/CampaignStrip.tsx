import { Link } from 'react-router-dom'
import { ArrowRight, Clock, Megaphone } from 'lucide-react'
import type { Campaign } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { track } from '../lib/events'
import { useCountdown } from '../lib/format'
import { locale, S } from '../strings'

/**
 * Campaigns are the promotions an admin schedules in the portal (title, line, image, link,
 * window, placement). Three renderings share one data shape:
 *   • CampaignStrip  — home, under the category tiles: up to two wide cards on desktop, one
 *                      snap-scrolling row on phones (finger-driven, never auto-scrolling)
 *   • CampaignHero   — the home hero for anonymous visitors when a campaign asks for it
 *   • CategoryBanner — a slim card at the top of one category's shelf
 * A campaign tied to a discount rule carries the rule's real end, so "Ends in" is never invented.
 * Sponsored slots are always labelled.
 */

// eslint-disable-next-line react-refresh/only-export-components
export function campaignText(c: Campaign) {
  const ar = locale.lang === 'ar'
  return {
    title: (ar && c.title_ar) || c.title,
    line: (ar && c.line_ar) || c.line || null,
    cta: (ar && c.cta_label_ar) || c.cta_label || S.campaign.cta,
  }
}

function click(c: Campaign, where: string) {
  track('rail_click', { meta: { rail: 'campaign', code: String(c.id), where } })
}

function Ends({ endsAt, className }: { endsAt?: string | null; className?: string }) {
  const t = useCountdown(endsAt)
  if (!t) return null
  return (
    <span className={cn('inline-flex items-center gap-1 text-xs font-medium', className)}>
      <Clock size={12} aria-hidden="true" /> {S.home.offerEnds(t)}
    </span>
  )
}

function Sponsored({ c, className }: { c: Campaign; className?: string }) {
  if (!c.sponsored) return null
  return <span className={cn('text-2xs font-semibold uppercase tracking-[0.08em]', className)}>{S.campaign.sponsored(c.sponsor_name || '')}</span>
}

/* ───────────────────────── strip card ───────────────────────── */

function StripCard({ c, wide, where }: { c: Campaign; wide: boolean; where: string }) {
  const t = campaignText(c)
  const external = c.cta_to.startsWith('http')
  const body = (
    <>
      {c.image_url ? (
        <img src={c.image_url} alt="" width={wide ? 220 : 120} height={wide ? 148 : 108} loading="lazy" decoding="async" className={cn('shrink-0 self-stretch bg-white object-cover', wide ? 'w-[220px]' : 'w-[120px]')} />
      ) : (
        <span className={cn('grid shrink-0 place-items-center self-stretch bg-plum text-white', wide ? 'w-[120px]' : 'w-[88px]')}>
          <Megaphone size={wide ? 28 : 22} aria-hidden="true" />
        </span>
      )}
      <span className="flex min-w-0 flex-1 flex-col justify-center gap-1 px-4 py-3.5">
        <span className="flex items-center gap-2">
          <Sponsored c={c} className="text-ink-3" />
          <Ends endsAt={c.ends_at} className="text-plum" />
        </span>
        <span className={cn('font-display font-bold leading-tight text-ink', wide ? 'text-lg' : 'text-[15px]')}>{t.title}</span>
        {t.line && <span className="line-clamp-2 text-sm leading-snug text-ink-2">{t.line}</span>}
        <span className="mt-1 inline-flex items-center gap-1 text-sm font-semibold text-plum">
          {t.cta} <ArrowRight size={14} aria-hidden="true" className="transition-transform duration-1 group-hover:translate-x-0.5 rtl:-scale-x-100" />
        </span>
      </span>
    </>
  )
  const cls = cn('group flex overflow-hidden rounded-lg border border-line bg-surface shadow-1 transition duration-2 ease-m hover:-translate-y-0.5 hover:shadow-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', wide ? 'h-[148px]' : 'h-[108px]')
  return external ? (
    <a href={c.cta_to} target="_blank" rel="noreferrer" onClick={() => click(c, where)} className={cls}>
      {body}
    </a>
  ) : (
    <Link to={c.cta_to} onClick={() => click(c, where)} className={cls}>
      {body}
    </Link>
  )
}

export function CampaignStrip({ campaigns, phone }: { campaigns: Campaign[]; phone: boolean }) {
  const list = campaigns.filter((c) => c.placement.includes('strip'))
  if (!list.length) return null
  if (phone) {
    return (
      <div className="mt-3 -mx-gutter">
        <ul className="flex snap-x snap-mandatory gap-3 overflow-x-auto px-gutter pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" aria-label={S.campaign.title}>
          {list.slice(0, 4).map((c) => (
            <li key={c.id} className="w-[88%] max-w-[420px] shrink-0 snap-center">
              <StripCard c={c} wide={false} where="strip" />
            </li>
          ))}
        </ul>
        {list.length > 1 && (
          <div className="mt-1.5 flex justify-center gap-1" aria-hidden="true">
            {list.slice(0, 4).map((c) => (
              <span key={c.id} className="h-1 w-1.5 rounded-full bg-line" />
            ))}
          </div>
        )}
      </div>
    )
  }
  return (
    <div className={cn('mt-4 grid gap-4', list.length > 1 ? 'md:grid-cols-2' : '')} aria-label={S.campaign.title}>
      {list.slice(0, 2).map((c) => (
        <StripCard key={c.id} c={c} wide where="strip" />
      ))}
    </div>
  )
}

/* ───────────────────────── hero takeover (anonymous only) ───────────────────────── */

export function CampaignHero({ c }: { c: Campaign }) {
  const t = campaignText(c)
  const external = c.cta_to.startsWith('http')
  const inner = (
    <>
      <div className="relative min-h-[240px] overflow-hidden bg-white lg:min-h-[340px]">
        {c.image_url ? (
          <img src={c.image_url} alt="" loading="eager" decoding="async" fetchPriority="high" className="absolute inset-0 h-full w-full object-contain p-6" />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-plum">
            <Megaphone size={64} aria-hidden="true" />
          </div>
        )}
      </div>
      <div className="flex flex-col justify-center gap-3 p-5 lg:p-8">
        <div className="flex items-center gap-3">
          <Sponsored c={c} className="text-ink-3" />
          <Ends endsAt={c.ends_at} className="text-plum" />
        </div>
        <h2 className="font-display text-2xl font-extrabold leading-[1.05] tracking-tight text-ink lg:text-4xl">{t.title}</h2>
        {t.line && <p className="max-w-prose text-base leading-snug text-ink-2">{t.line}</p>}
        <span className="mt-2 inline-flex h-12 w-fit items-center gap-2 rounded-md bg-plum px-5 text-base font-semibold text-white transition duration-1 ease-m group-hover:bg-plum-deep">
          {t.cta} <ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />
        </span>
      </div>
    </>
  )
  const cls = 'group mt-4 grid overflow-hidden rounded-xl border border-line bg-surface shadow-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  return external ? (
    <a href={c.cta_to} target="_blank" rel="noreferrer" onClick={() => click(c, 'hero')} className={cls}>
      {inner}
    </a>
  ) : (
    <Link to={c.cta_to} onClick={() => click(c, 'hero')} className={cls}>
      {inner}
    </Link>
  )
}

/* ───────────────────────── category banner ───────────────────────── */

export function CategoryBanner({ campaigns, category }: { campaigns: Campaign[]; category: string }) {
  const c = campaigns.find((x) => x.placement.includes('category') && (x.category || '').toUpperCase() === category.toUpperCase())
  if (!c) return null
  return (
    <div className="mb-3">
      <StripCard c={c} wide={false} where="category" />
    </div>
  )
}
