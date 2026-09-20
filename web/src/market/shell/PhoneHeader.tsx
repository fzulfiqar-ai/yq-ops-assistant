import { useLayoutEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, Search } from 'lucide-react'
import { SearchHints } from '../components/SearchField'
import { useMarket } from '../MarketContext'
import { readCustomer } from '../lib/device'
import { initials } from '../lib/format'
import { NAV_ICONS } from '../lib/icons'
import { S } from '../strings'
import { IconButton } from '../ui/Button'
import { useShell, type PageTitle } from './ShellContext'

/**
 * Phone + tablet top of the page.
 *
 * Band pages (useSearchBand: Home, Browse, a category shelf) get the plum band: ONE sticky <header>
 * whose `top` is minus the brand row's height, so the brand row (logo + kicker, or back + title)
 * rides away with the page while the white search pill under it stays pinned — no scroll handler.
 * The shell writes the measured brand row to `--m-brand-h` and the pinned part (search row + top
 * safe area) to `--m-search-h` so pages can pin their own rows under the band.
 *
 * Other pages keep the light header: a back arrow and the page title (or the brand row), scrolling
 * away with the page — the floating nav is the anchor there.
 */
export function PhoneHeader() {
  const { title, searchBand } = useShell()
  const { rep } = useMarket()
  const navigate = useNavigate()
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate('/'))

  if (searchBand) return <BandHeader title={title} onBack={back} />

  if (title) {
    return (
      <header className="flex h-14 items-center gap-1 px-2">
        {title.back ? (
          <IconButton label={S.states.back} onClick={back}>
            <ArrowLeft size={20} aria-hidden="true" />
          </IconButton>
        ) : (
          <span className="w-2" />
        )}
        <h1 className="min-w-0 flex-1 truncate font-display text-lg font-bold text-ink">{title.title}</h1>
      </header>
    )
  }

  return (
    <header className="flex h-14 items-center gap-3 px-gutter">
      <Link to="/" aria-label={`${S.brand} · ${S.kicker}`} className="flex min-w-0 items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
        <img src="/yq-logo-160.webp" alt="" width={36} height={36} className="h-9 w-9 shrink-0 rounded-sm" />
        <span className="min-w-0">
          <span className="block truncate font-display text-md font-bold leading-tight text-ink">{S.brand}</span>
          <span className="block truncate text-2xs text-ink-2">{S.kicker}</span>
        </span>
      </Link>
      <span className="flex-1" />
      <Link
        to="/me"
        aria-label={rep ? `${S.rep.yours}: ${rep.name}` : S.nav.me}
        className="grid h-11 w-11 shrink-0 place-items-center overflow-hidden rounded-full bg-plum-soft text-xs font-bold text-plum-ink ring-1 ring-plum/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
      >
        {rep?.photo_url ? <img src={rep.photo_url} alt="" width={44} height={44} className="h-full w-full object-cover" /> : rep ? initials(rep.name) : 'YQ'}
      </Link>
    </header>
  )
}

const ringOnPlum = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/90'

function BandHeader({ title, onBack }: { title: PageTitle | null; onBack: () => void }) {
  const { panelCode, paletteOpen } = useShell()
  const { rep } = useMarket()
  const headerRef = useRef<HTMLElement>(null)
  const rowRef = useRef<HTMLDivElement>(null)
  const titled = Boolean(title)

  // --m-brand-h drives the sticky offset; --m-search-h is what stays pinned (incl. the safe area).
  // Re-measured when the first row swaps between brand and title (a different element).
  useLayoutEffect(() => {
    const header = headerRef.current
    const row = rowRef.current
    if (!header || !row) return
    const root = document.documentElement.style
    const apply = () => {
      const brand = row.offsetHeight
      root.setProperty('--m-brand-h', `${brand}px`)
      root.setProperty('--m-search-h', `${Math.max(0, header.offsetHeight - brand)}px`)
    }
    apply()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(apply)
    ro?.observe(header)
    ro?.observe(row)
    return () => {
      ro?.disconnect()
      root.removeProperty('--m-brand-h')
      root.removeProperty('--m-search-h')
    }
  }, [titled])

  const shop = readCustomer().shop.trim()
  const MeIcon = NAV_ICONS.me

  return (
    <header
      ref={headerRef}
      className="band-sash sash-corner sticky z-header rounded-b-[22px] bg-plum text-white"
      style={{ top: 'calc(-1 * var(--m-brand-h, 0px))', paddingTop: 'var(--m-safe-t)' }}
    >
      {/* under a notch the last slice of the brand row would peek out above the pinned search */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-x-0 top-0 z-[1] h-[var(--m-safe-t)] bg-plum" />

      {title ? (
        <div ref={rowRef} className="flex h-[60px] items-center gap-1 px-2 pt-1">
          {title.back ? (
            <button type="button" onClick={onBack} aria-label={S.states.back} title={S.states.back} className={`grid h-11 w-11 shrink-0 place-items-center rounded-sm text-white transition duration-1 ease-m hover:bg-white/10 active:scale-95 ${ringOnPlum}`}>
              <ArrowLeft size={21} strokeWidth={2} aria-hidden="true" />
            </button>
          ) : (
            <span className="w-[calc(var(--m-gutter)-0.5rem)] shrink-0" />
          )}
          <h1 className="min-w-0 flex-1 truncate font-display text-lg font-bold text-white">{title.title}</h1>
        </div>
      ) : (
        <div ref={rowRef} className="flex h-16 items-center gap-3 px-gutter pt-1">
          <Link to="/" aria-label={`${S.brand} · ${S.kicker}`} className={`flex min-w-0 items-center gap-3 rounded-md ${ringOnPlum}`}>
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-[13px] bg-white shadow-[0_8px_18px_-8px_rgba(24,10,40,0.6)] ring-4 ring-white/10">
              <img src="/yq-logo-160.webp" alt="" width={36} height={36} className="h-9 w-9 rounded-[9px]" />
            </span>
            <span className="min-w-0">
              <span className="block truncate font-display text-[17px] font-bold leading-[22px] text-white">{S.brand}</span>
              <span className="block truncate text-xs text-white/90">{shop ? S.splash.welcome(shop) : S.kicker}</span>
            </span>
          </Link>
          <span className="flex-1" />
          <Link to="/me" aria-label={rep ? `${S.rep.yours}: ${rep.name}` : S.nav.me} className={`grid h-11 w-11 shrink-0 place-items-center rounded-full ${ringOnPlum}`}>
            <span className="grid h-10 w-10 place-items-center overflow-hidden rounded-full bg-white/15 text-xs font-bold text-white ring-1 ring-white/30">
              {rep?.photo_url ? <img src={rep.photo_url} alt="" width={40} height={40} className="h-full w-full object-cover" /> : rep ? initials(rep.name) : <MeIcon size={19} strokeWidth={1.9} aria-hidden="true" />}
            </span>
          </Link>
        </div>
      )}

      {/* the pinned part: one link to /search (single tab stop), the hint rotating inside */}
      <div className="px-gutter pb-3 pt-2.5">
        <Link
          to="/search"
          aria-label={S.search.band}
          className="flex h-12 items-center gap-2.5 rounded-[14px] bg-white pe-1.5 ps-3.5 text-ink shadow-[0_10px_24px_-12px_rgba(24,10,40,0.55)] transition duration-1 ease-m active:scale-[.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-plum"
        >
          <Search size={19} strokeWidth={2.1} className="shrink-0 text-plum" aria-hidden="true" />
          <SearchHints hints={S.search.hints} paused={Boolean(panelCode) || paletteOpen} className="min-w-0 flex-1 text-[15px]" leadClassName="text-ink-3" hintClassName="font-medium text-ink-2" />
          <span aria-hidden="true" className="inline-flex h-9 shrink-0 items-center rounded-[10px] bg-ink px-4 text-sm font-semibold text-white">
            {S.search.submit}
          </span>
        </Link>
      </div>
    </header>
  )
}
