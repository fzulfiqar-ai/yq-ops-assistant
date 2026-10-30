import type { BadgeTone } from '@/components/ui/badge'
import { isReadOnly, useAuth } from '@/lib/auth'
import type { MiActionStatus, MiCaps, MiCaseStatus, MiReportStatus } from '@/lib/marketIntel'

/**
 * Shared pieces of the Market Intel pages (release r8m): the form classes the v1 page uses, the
 * query-string helper, the badge tones of the new statuses, and who may do what on this login.
 */

export const SELECT =
  'h-10 rounded-xl border border-border bg-card px-3 text-[13px] text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40'
/** a filter in a row of filters: a full cell of the two-column grid on a phone, its own width from 640 px */
export const FILTER = `${SELECT} w-full min-w-0 sm:w-auto`
export const INPUT =
  'h-10 w-full rounded-xl border border-border bg-card px-3 text-[13.5px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40'
export const TEXTAREA =
  'w-full resize-y rounded-xl border border-border bg-card px-3 py-2 text-[13.5px] leading-snug text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40'
export const BTN =
  'inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-[13.5px] font-semibold text-primary-foreground transition hover:bg-primary-deep disabled:opacity-45'
export const BTN_OUTLINE =
  'inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-[13px] font-semibold text-foreground transition hover:bg-accent disabled:opacity-45'
export const BTN_GHOST =
  'inline-flex h-9 items-center gap-1 rounded-lg px-2.5 text-[12.5px] font-semibold text-primary transition hover:bg-accent disabled:opacity-45'
export const LABEL = 'mb-1 block text-[11px] font-semibold uppercase tracking-[0.07em] text-muted-foreground'
export const CARD = 'rounded-2xl border border-border bg-card p-4'

/**
 * "Seen in the last n days" as a day (YYYY-MM-DD), not a timestamp: the same all day, so a filter
 * does not change the board's query on every render (v1 sent milliseconds and refetched in a loop).
 */
export function sinceDay(days: string, now: number = Date.now()): string {
  const n = Number(days)
  if (!n) return ''
  const d = new Date(now - n * 86_400_000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** "?a=1&b=x" from the set values only ('' when none). */
export function qs(p: Record<string, string | number | null | undefined>): string {
  const u = new URLSearchParams()
  Object.entries(p).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') u.set(k, String(v))
  })
  const s = u.toString()
  return s ? `?${s}` : ''
}

export const CASE_STATUS_TONE: Record<MiCaseStatus, BadgeTone> = {
  submitted: 'accent',
  under_review: 'amber',
  more_info_required: 'amber',
  verified: 'green',
  not_substantiated: 'grey',
  action_requested: 'ink',
  resolved: 'green',
  closed: 'grey',
}

export const REPORT_STATUS_TONE: Record<MiReportStatus, BadgeTone> = { draft: 'amber', approved: 'green', superseded: 'grey' }

export const ACTION_STATUS_TONE: Record<MiActionStatus, BadgeTone> = { open: 'amber', responded: 'accent', resolved: 'green', cancelled: 'grey' }

/** "14 Oct 2026" (a date or a timestamp) */
export function dayText(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso)
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** "14 Oct, 10:20" */
export function whenText(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? String(iso)
    : `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`
}

/** A due date (YYYY-MM-DD) that has passed: the end of that day is behind us. */
export function isPast(day?: string | null, now: number = Date.now()): boolean {
  if (!day) return false
  const d = new Date(`${day.slice(0, 10)}T23:59:59`)
  return !Number.isNaN(d.getTime()) && d.getTime() < now
}

/** The login part of an e-mail the API sends as an actor. */
export function who(actor?: string | null): string {
  return String(actor || '').split('@')[0] || 'Someone'
}

export interface MiRoles {
  /** any role but salesman (a storekeeper granted the page counts as office) */
  office: boolean
  /** REVIEW_ROLES: verify, move statuses, ask the rep, actions, build reports */
  review: boolean
  /** APPROVE_ROLES (reviewers and management): approve and download supplier reports */
  approve: boolean
  admin: boolean
  /** management: reads everything, changes nothing but an approval */
  readOnly: boolean
}

/** The server's capabilities when a payload carried them; the roles of app/market_intel.py otherwise. */
export function useMiRoles(caps?: MiCaps | null): MiRoles {
  const { me } = useAuth()
  const role = String(me?.role || '')
  const readOnly = isReadOnly(me)
  const review = !readOnly && (caps?.can_review ?? ['admin', 'member', 'operations', 'sales_manager'].includes(role))
  const approve = caps?.can_approve ?? (review || role === 'management')
  const admin = caps?.can_admin ?? caps?.is_admin ?? role === 'admin'
  return { office: role !== 'salesman', review, approve, admin, readOnly }
}
