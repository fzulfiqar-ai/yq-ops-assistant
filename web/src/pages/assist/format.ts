import type { BadgeTone } from '@/components/ui/badge'

/** The data and formatters of the portal's YQ Assist page (web/src/pages/MerchantVoice.tsx and its tabs). */

/** Bahrain wall-clock time, like the rest of the portal. */
export function bhTime(ts?: string | null): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('en-GB', {
      timeZone: 'Asia/Bahrain', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    })
  } catch {
    return String(ts)
  }
}

/** "5 h" · "2.1 days": business hours (Friday and Saturday never count), as the server measured them. */
export function hoursText(h?: number | null): string {
  if (h == null) return '—'
  return h < 48 ? `${Math.round(h)} h` : `${(h / 24).toFixed(1)} days`
}

export function msText(ms?: number | null): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

export const SESSION_OUTCOME: Record<string, { label: string; tone: BadgeTone }> = {
  solved: { label: 'Solved', tone: 'green' },
  ordered: { label: 'Ordered', tone: 'green' },
  ticket: { label: 'Ticket', tone: 'rose' },
  escalated: { label: 'To the rep', tone: 'accent' },
  missed: { label: 'Not stocked', tone: 'amber' },
  failed: { label: 'AI failed', tone: 'grey' },
}

export const TURN_OUTCOME: Record<string, BadgeTone> = {
  answered: 'green', miss: 'amber', fallback: 'grey', handoff: 'accent', capped: 'rose', error: 'rose', ticket: 'rose',
}

export const TICKET_KIND: Record<string, string> = {
  damaged: 'Damaged', wrong_item: 'Wrong item', missing: 'Missing pieces', not_working: 'Not working',
  delivery: 'Delivery problem', return: 'Return', other: 'Other', callback: 'Call back', account: 'Account / credit',
}

export const TICKET_STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  new: { label: 'New', tone: 'rose' },
  in_progress: { label: 'In progress', tone: 'amber' },
  waiting_shop: { label: 'Waiting for shop', tone: 'accent' },
  resolved: { label: 'Resolved', tone: 'green' },
  rejected: { label: 'Not valid', tone: 'grey' },
}

export const RESOLUTIONS: { id: string; label: string }[] = [
  { id: 'replaced', label: 'Replaced' },
  { id: 'credit_note', label: 'Credit note' },
  { id: 'returned', label: 'Returned' },
  { id: 'answered', label: 'Answered / sorted' },
  { id: 'not_valid', label: 'Not valid' },
  { id: 'other', label: 'Other' },
]

export const POLICY_TOPIC: Record<string, string> = {
  returns: 'Returns and exchanges', warranty: 'Warranty', payment: 'Payment methods', credit: 'Credit terms',
  delivery: 'Delivery times and areas', hours: 'Working hours', account: 'Opening an account', invoice: 'Invoices and VAT',
  other: 'Other',
}
