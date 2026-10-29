import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check, LifeBuoy, MessageCircle } from 'lucide-react'
import { apiPatch } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { Badge } from '@/components/ui/badge'
import { relTime, type ChatTicket } from './lib'

/**
 * "From the chat" on the rep's Today (YQ Assist v2, owner 29-Oct-2026: the order's rep owns the ticket). Each
 * row: the reference, what went wrong, the items, the order and the shop, a WhatsApp button to the shop when the
 * order carries its number, "On it" and "Done as ..." (a resolution is required). The office sees every ticket
 * on YQ Assist → Tickets and is reminded about any nobody opened.
 */
const DONE: { value: string; label: string; status: 'resolved' | 'rejected' }[] = [
  { value: 'replaced', label: 'Replaced', status: 'resolved' },
  { value: 'credit_note', label: 'Credit note', status: 'resolved' },
  { value: 'answered', label: 'Answered / sorted', status: 'resolved' },
  { value: 'not_valid', label: 'Not valid', status: 'rejected' },
]

export function ChatTickets({ tickets }: { tickets: ChatTicket[] }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState<number | null>(null)
  if (!tickets.length) return null

  const move = async (t: ChatTicket, body: Record<string, string>, said: string) => {
    setBusy(t.id)
    try {
      await apiPatch(`/shop/me/tickets/${t.id}`, body)
      void qc.invalidateQueries({ queryKey: ['shop-today'] })
      toast(`${t.ref}: ${said}`, 'success')
    } catch {
      toast('Could not update the ticket', 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="flex items-center gap-2 px-4 py-3">
        <LifeBuoy size={16} className="text-primary" aria-hidden="true" />
        <h2 className="font-display text-[15px] font-bold">From the chat</h2>
        <span className="text-[12px] text-muted-foreground">problems and call-backs your shops reported</span>
      </div>
      <ul className="divide-y divide-border border-t border-border">
        {tickets.slice(0, 10).map((t) => (
          <li key={t.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-[13.5px] font-semibold">{t.label}</span>
                <span className="font-mono text-[11.5px] text-muted-foreground">{t.ref}</span>
                {t.status === 'new' ? <Badge tone="rose">New</Badge> : <Badge tone="amber">{t.status === 'in_progress' ? 'On it' : 'Waiting for shop'}</Badge>}
              </span>
              <span className="block text-[11.5px] text-muted-foreground">
                {[t.shop, t.order_no, t.lines.map((l) => `${l.item_code} ×${l.qty}`).join(', '), relTime(t.created_at)].filter(Boolean).join(' · ')}
              </span>
              {t.note && <span className="mt-0.5 block text-[12px] text-foreground/80">“{t.note}”</span>}
            </span>
            {t.wa_url && (
              <a href={t.wa_url} target="_blank" rel="noreferrer" aria-label="WhatsApp the shop" title="WhatsApp the shop" className="grid h-10 w-10 place-items-center rounded-xl border border-border text-[#1d9e50] hover:bg-muted">
                <MessageCircle size={16} aria-hidden="true" />
              </a>
            )}
            {t.status === 'new' && (
              <button type="button" disabled={busy === t.id} onClick={() => move(t, { status: 'in_progress' }, 'on it')} className="h-10 rounded-xl border border-border px-3 text-[12.5px] font-semibold hover:bg-muted disabled:opacity-50">
                On it
              </button>
            )}
            <label className="relative">
              <span className="sr-only">Done as</span>
              <select
                disabled={busy === t.id}
                value=""
                onChange={(e) => {
                  const d = DONE.find((x) => x.value === e.target.value)
                  if (d) void move(t, { status: d.status, resolution: d.value }, d.label.toLowerCase())
                }}
                className="h-10 rounded-xl border border-border bg-card pe-8 ps-3 text-[12.5px] font-semibold hover:bg-muted disabled:opacity-50"
              >
                <option value="">Done as…</option>
                {DONE.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
              <Check size={14} aria-hidden="true" className="pointer-events-none absolute end-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            </label>
          </li>
        ))}
      </ul>
    </section>
  )
}
