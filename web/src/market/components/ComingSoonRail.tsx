import { useUpcoming } from '../lib/upcoming'
import { locale } from '../strings'
import { ComingSoonCard } from './ComingSoonCard'
import { upcomingCopy } from './ComingSoonShared'
import { Rail } from './Rail'

/**
 * Home rail "Coming soon · WEKOME" — mounted by HomeBelow AFTER the in-stock rails, as its own
 * lazy chunk: the payload is its own request (never part of the catalog download, so the first
 * screen's LCP does not wait for it) — the ONE request lib/upcoming shares with the home slide and
 * the aside, usually answered by the time this mounts — and the rail renders nothing at all until
 * there is at least one published card (a failed request: no rail). The title's brand and the
 * subtitle's month are payload data, not copy.
 */
export default function ComingSoonRail() {
  const t = upcomingCopy()
  const data = useUpcoming()
  if (!data) return null
  const when = locale.lang === 'ar' ? data.expected_label_ar : data.expected_label_en
  return (
    <Rail id="upcoming" title={t.rail(data.brand)} subtitle={`${when} · ${t.tagline}`} seeAllTo={`/brands/${encodeURIComponent(data.brand.toLowerCase())}`} max={6}>
      {data.items.map((it) => (
        <ComingSoonCard key={it.id} item={it} variant="compact" />
      ))}
    </Rail>
  )
}
