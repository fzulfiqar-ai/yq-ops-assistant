import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useMarket } from '../MarketContext'
import { initials } from '../lib/format'
import { S } from '../strings'
import { IconButton } from '../ui/Button'
import { useShell } from './ShellContext'

/**
 * Phone top row. Home: the mark, the wordmark and the rep avatar (→ My YQ). Other pages: a back
 * arrow and the page title. It scrolls away with the page — the floating nav is the anchor.
 */
export function PhoneHeader() {
  const { title } = useShell()
  const { rep } = useMarket()
  const navigate = useNavigate()
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate('/'))

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
      <Link to="/" aria-label={`${S.brand} · ${S.tagline}`} className="flex min-w-0 items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
        <img src="/yq-logo-160.webp" alt="" width={36} height={36} className="h-9 w-9 shrink-0 rounded-sm" />
        <span className="min-w-0">
          <span className="block truncate font-display text-md font-bold leading-tight text-ink">{S.brand}</span>
          <span className="block truncate text-2xs text-ink-2">{S.tagline}</span>
        </span>
      </Link>
      <span className="flex-1" />
      <Link
        to="/me"
        aria-label={rep ? `${S.rep.yours}: ${rep.name}` : S.nav.me}
        className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-full bg-plum-soft text-xs font-bold text-plum-ink ring-1 ring-plum/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
      >
        {rep?.photo_url ? <img src={rep.photo_url} alt="" width={40} height={40} className="h-full w-full object-cover" /> : rep ? initials(rep.name) : 'YQ'}
      </Link>
    </header>
  )
}
