import { useQuery } from '@tanstack/react-query'
import { FlaskConical, Info } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * "Does the follow-up list work?" — the hold-out readout on the Salesmen page (release R7d, plan §23
 * item 4, audit OFF-16). Read-only. One shop in five is held back from each rep's Due list
 * (app/followups.py); app/followup_lift.py records, once per rep per day, which shops were served and
 * which were held back, and v_followup_lift checks Focus for an Accessories invoice within 14 days.
 *
 * Only complete 14-day windows are totalled (the API does it). The card says plainly how small the
 * sample is: with a few dozen shops a week, only a large, lasting gap means anything.
 */

interface LiftArm { shops: number; bought_14d: number; net_bhd_14d: number; buy_rate_pct: number | null; net_bhd_per_shop: number | null }
interface LiftRow { week_start: string; salesman_name: string | null; arm: 'served' | 'holdout'; shops: number; bought_14d: number; tapped: number; complete: boolean }
interface LiftResp { available: boolean; rows: LiftRow[]; totals: Partial<Record<'served' | 'holdout', LiftArm>>; note?: string }

const bhd3 = (n?: number | null) =>
  n == null ? '—' : `BHD ${n.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`
const pct = (n?: number | null) => (n == null ? '—' : `${n.toFixed(1)} %`)

function Arm({ title, sub, a }: { title: string; sub: string; a?: LiftArm }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{title}</div>
      <div className="mt-1 font-display text-xl font-extrabold tabular-nums text-[#6D4091] dark:text-[#c7a6e6]">{pct(a?.buy_rate_pct)}</div>
      <div className="text-[12px] text-muted-foreground">
        bought within 14 days · {a ? `${a.bought_14d} of ${a.shops} shops` : 'no shops yet'}
      </div>
      <div className="mt-1 text-[12px] tabular-nums text-muted-foreground">{bhd3(a?.net_bhd_per_shop)} a shop · {sub}</div>
    </div>
  )
}

export function FollowupLiftCard() {
  const q = useQuery({ queryKey: ['shop-followups-lift'], queryFn: () => apiGet<LiftResp>('/shop/followups/lift'), staleTime: 10 * 60_000, retry: 1 })
  const d = q.data
  const served = d?.totals?.served
  const held = d?.totals?.holdout
  const taps = (d?.rows || []).filter((r) => r.arm === 'served').reduce((s, r) => s + (r.tapped || 0), 0)
  const openWeeks = (d?.rows || []).filter((r) => !r.complete).length
  return (
    <Card className="mt-6 p-5">
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[#F3ECF8] text-[#6D4091] dark:bg-[#6D4091]/20 dark:text-[#c7a6e6]">
          <FlaskConical size={17} aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <h2 className="font-display text-[15px] font-bold">Does the follow-up list work?</h2>
          <p className="mt-0.5 max-w-2xl text-[12.5px] text-muted-foreground">
            Shops on a rep's Due list against the 1 in 5 held back from it: how many bought (a Focus Accessories invoice) within 14 days.
          </p>
        </div>
      </div>
      {q.isLoading ? (
        <Skeleton className="mt-4 h-24 rounded-xl" />
      ) : q.isError || !d ? (
        <p className="mt-4 text-[13px] text-muted-foreground">Could not load the readout just now.</p>
      ) : !d.available ? (
        <p className="mt-4 text-[13px] text-muted-foreground">Not measured yet — it starts with the next update, the first time each rep opens his Due list.</p>
      ) : !served && !held ? (
        <p className="mt-4 text-[13px] text-muted-foreground">
          No complete 14-day window yet{openWeeks ? ` (${openWeeks} week${openWeeks === 1 ? '' : 's'} still open)` : ''} — the first readout lands two weeks after the reps open their Due lists.
        </p>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Arm title="On the Due list" sub={`${taps} shop${taps === 1 ? '' : 's'} tapped`} a={served} />
            <Arm title="Held back" sub="never shown to the rep" a={held} />
          </div>
          {openWeeks > 0 && <p className="mt-2 text-[12px] text-muted-foreground">{openWeeks} rep-week{openWeeks === 1 ? '' : 's'} still inside the 14 days — not counted yet.</p>}
        </>
      )}
      {d?.note && (
        <p className="mt-3 flex items-start gap-1.5 text-[11.5px] text-muted-foreground">
          <Info size={12} className="mt-[1px] shrink-0" aria-hidden="true" />{d.note}
        </p>
      )}
    </Card>
  )
}
