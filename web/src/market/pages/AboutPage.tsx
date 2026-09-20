import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ClipboardList, LayoutGrid, ListChecks, MessageCircle, ShieldCheck, Tag, Truck, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { RepCard } from '../components/RepCard'
import { useEdgeFade } from '../hooks/useEdgeFade'
import { useMarket } from '../MarketContext'
import { bhd } from '../lib/format'
import { usePageTitle } from '../shell/ShellContext'
import { useReducedMotion } from '../shell/useViewport'
import { S } from '../strings'
import { LinkButton } from '../ui/Button'

/**
 * /about — the trust page of a wholesale supplier: who YQ is, trade prices and the wholesale
 * minimum, how ordering works, delivery and returns, privacy, contact. One scrolling page with
 * anchors (#trade, #how, #delivery, #privacy, #contact) — the promise bar, the footer and My YQ
 * deep-link into it; every section keeps clear of the sticky desktop header (scroll-margin).
 * Nothing here promises more than the settings say: no "no minimum", free delivery only when it is.
 */

// clears the pinned “On this page” row on a phone (h-10 + py-2) and, on desktop, the MEASURED
// sticky header (--m-sticky-h, written by StickyHeader; the fixed row height is the fallback) PLUS
// that same row, which now pins under it (1rem offset + 40px pills + py-2 both sides + 8px air)
const SCROLL_M = 'scroll-mt-[68px] lg:scroll-mt-[calc(var(--m-sticky-h,var(--m-header-h))+5rem)]'

function Section({ id, icon: Icon, title, children }: { id: string; icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={`${SCROLL_M} rounded-xl bg-surface p-5 shadow-1 ring-1 ring-line lg:p-6`}>
      <h2 id={`${id}-title`} className="flex items-center gap-2.5 font-display text-lg font-bold text-ink">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-plum-soft text-plum">
          <Icon size={17} strokeWidth={2} />
        </span>
        {title}
      </h2>
      <div className="mt-3 space-y-2.5 text-sm leading-relaxed text-ink-2">{children}</div>
    </section>
  )
}

export default function AboutPage() {
  const { rep, data, settings } = useMarket()
  const { hash, key } = useLocation()
  const reduced = useReducedMotion()
  const navRef = useRef<HTMLElement>(null)
  const mask = useEdgeFade(navRef, true, [hash])
  const current = decodeURIComponent(hash.slice(1))
  usePageTitle(S.about.title, true, `${S.about.title} · ${S.brand}`)

  /* ── the pinned row must say it is pinned ──
   * On a phone this row sits at the top of the viewport with the page's cards sliding under it, and
   * with a flat cream background and no edge the card above looked amputated. It is stuck exactly
   * when it can no longer sit fully inside a viewport shortened by 1px — no sentinel, no scroll
   * handler. On desktop it pins under the measured header instead, where it keeps its hairline. */
  const [stuck, setStuck] = useState(false)
  useEffect(() => {
    const el = navRef.current
    if (!el || typeof IntersectionObserver !== 'function') return
    const io = new IntersectionObserver(([entry]) => setStuck(!entry.isIntersecting && entry.boundingClientRect.top < 1), { threshold: [1], rootMargin: '-1px 0px 0px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  useEffect(() => {
    if (!hash) return
    const el = document.getElementById(decodeURIComponent(hash.slice(1)))
    if (el) el.scrollIntoView({ block: 'start', behavior: reduced ? 'auto' : 'smooth' })
  }, [hash, key, reduced])

  const threshold = Number(settings.free_delivery_threshold_bhd || 0)
  const fee = Number(settings.delivery_fee_bhd || 0)
  const delivery = threshold > 0 ? S.about.deliveryOver(threshold.toFixed(3)) : fee > 0 ? S.about.deliveryFee(bhd(fee)) : S.about.deliveryFree
  const min = Number(settings.min_order_bhd || 0)
  const mode = settings.small_order_mode || 'request'
  const minimum = min > 0 ? (mode === 'block' ? S.about.minimumBlock(bhd(min)) : mode === 'allow' ? S.about.minimumAllow(bhd(min)) : S.about.minimum(bhd(min))) : null

  const sections: { id: string; label: string }[] = [
    { id: 'trade', label: S.about.trade },
    { id: 'how', label: S.about.how },
    { id: 'delivery', label: S.about.delivery },
    { id: 'privacy', label: S.about.privacy },
    { id: 'contact', label: S.about.contact },
  ]

  return (
    <div className="px-gutter lg:px-0">
      {/* trailing space so the last anchors (#delivery, #privacy, #contact) can reach the top */}
      <div className="mx-auto max-w-3xl space-y-4 pb-[40vh] lg:mx-0 lg:mt-4">
        <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.about.title}</h1>

        {/* who we are — the one plum surface on the page */}
        <section id="about" aria-labelledby="about-title" className={`${SCROLL_M} band-sash sash-corner overflow-hidden rounded-xl bg-plum p-5 text-white lg:p-6`}>
          <div className="flex items-center gap-3">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-white shadow-[0_8px_18px_-8px_rgba(24,10,40,0.6)] ring-4 ring-white/10">
              <img src="/yq-logo-160.webp" alt="" width={38} height={38} className="h-[38px] w-[38px] rounded-[10px]" />
            </span>
            <div className="min-w-0">
              <h2 id="about-title" className="font-display text-xl font-bold leading-tight text-white">
                {S.me.about}
              </h2>
              <p className="truncate text-xs text-white/80">{S.kicker}</p>
            </div>
          </div>
          <p className="mt-4 max-w-prose text-[15px] leading-relaxed text-white/95">{S.about.intro}</p>
          <p className="mt-3 text-xs leading-snug text-white/75">{S.about.company}</p>
        </section>

        {/* on this page — pinned everywhere, so there is a way back between sections all the way down */}
        <nav
          ref={navRef}
          data-stuck={stuck ? '' : undefined}
          aria-label={S.about.onPage}
          style={mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined}
          className="no-scrollbar sticky top-0 z-[29] -mx-gutter flex gap-2 overflow-x-auto border-b border-transparent bg-canvas px-gutter py-2 transition-[border-color,box-shadow] duration-2 ease-m data-[stuck]:border-line data-[stuck]:shadow-[0_10px_18px_-16px_hsl(268_30%_10%/0.45)] lg:top-[calc(var(--m-sticky-h,var(--m-header-h))+1rem)] lg:mx-0 lg:flex-wrap lg:overflow-visible lg:border-line lg:px-0 lg:shadow-none"
        >
          {sections.map((s) => (
            <Link
              key={s.id}
              to={{ hash: s.id }}
              aria-current={current === s.id ? 'location' : undefined}
              className={cn(
                'inline-flex h-10 shrink-0 items-center whitespace-nowrap rounded-full px-3.5 text-sm font-semibold shadow-1 transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                current === s.id ? 'bg-ink text-white' : 'bg-surface text-ink ring-1 ring-inset ring-line hover:bg-plum-wash hover:ring-ink/15',
              )}
            >
              {s.label}
            </Link>
          ))}
          <span aria-hidden="true" className="w-px shrink-0 lg:hidden" />
        </nav>

        <Section id="trade" icon={Tag} title={S.about.trade}>
          <p>{S.about.tradeText}</p>
          {minimum && <p className="rounded-md bg-plum-wash px-3.5 py-2.5 font-medium text-plum-ink">{minimum}</p>}
          <p>{S.about.tradeConfirm}</p>
        </Section>

        <Section id="how" icon={ListChecks} title={S.about.how}>
          <ol className="relative space-y-3">
            {S.about.steps.map((step, i) => (
              <li key={step} className="relative flex gap-3">
                {i < S.about.steps.length - 1 && <span aria-hidden="true" className="absolute start-[13px] top-7 h-[calc(100%-12px)] w-px bg-line" />}
                <span className="relative grid h-7 w-7 shrink-0 place-items-center rounded-full bg-plum font-display text-xs font-bold text-white">
                  <span className="sr-only">{S.about.step(i + 1)}</span>
                  <span aria-hidden="true">{i + 1}</span>
                </span>
                <span className="pt-1 text-ink">{step}</span>
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2 pt-2">
            <LinkButton to="/quick" variant="primary" size="sm" icon={<ClipboardList size={15} aria-hidden="true" />}>
              {S.band.paste}
            </LinkButton>
            <LinkButton to="/shop" variant="secondary" size="sm" icon={<LayoutGrid size={15} aria-hidden="true" />}>
              {S.nav.browse}
            </LinkButton>
          </div>
        </Section>

        <Section id="delivery" icon={Truck} title={S.about.delivery}>
          <p className="font-medium text-ink">{delivery}</p>
          <p>{S.about.deliveryHow}</p>
          <p>{S.about.returns}</p>
        </Section>

        <Section id="privacy" icon={ShieldCheck} title={S.about.privacy}>
          <p>{S.about.privacyText}</p>
          <p>
            <Link to="/me" className="font-semibold text-plum underline-offset-2 hover:underline">
              {S.about.privacyForget}
            </Link>
          </p>
        </Section>

        <Section id="contact" icon={MessageCircle} title={S.about.contact}>
          {/* the rep card carries the WhatsApp button */}
          {rep ? <RepCard rep={rep} /> : <p>{S.rep.soon}</p>}
          <p>{data?.company || S.company}</p>
        </Section>
      </div>
    </div>
  )
}
