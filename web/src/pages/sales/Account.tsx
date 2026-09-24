import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Check, ChevronRight, Copy, ExternalLink, KeyRound, Loader2, LogOut, MessageCircle, QrCode } from 'lucide-react'
import { mustResetOf, useAuth } from '@/lib/auth'
import { navFor } from '@/lib/nav'
import { changeOwnPassword } from '@/lib/password'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { bhd3, bhdStr, dayLabel, initials, monthName, useAuthedBlob, useShopMe } from './lib'

/**
 * /account — who I am, my storefront link and QR, my target, my password, everything else the
 * office has switched on for me, and sign out. Deliberately short: a salesman opens this once.
 */
export default function Account() {
  const { me, session, signOut, refreshMe } = useAuth()
  const toast = useToast()
  const meQ = useShopMe()
  // On a temporary password the API does not ask for the current one (the rep typed it a minute
  // ago); otherwise it is required so a stolen token alone cannot change it.
  const mustReset = mustResetOf(me, session)
  const [p0, setP0] = useState('')
  const [p1, setP1] = useState('')
  const [p2, setP2] = useState('')
  const [busy, setBusy] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const [qrOpen, setQrOpen] = useState(false)
  const { blobUrl: qr } = useAuthedBlob(qrOpen ? meQ.data?.qr_url : null)
  const name = me?.full_name || meQ.data?.salesman?.name || me?.email?.split('@')[0] || ''
  const link = meQ.data?.link || ''
  const sm = meQ.data?.salesman
  const focus = meQ.data?.focus
  const CORE = new Set(['/today', '/shop', '/shop-orders', '/customers', '/account', '/settings', '/catalog', '/'])
  const more = navFor(me).filter((n) => !CORE.has(n.to))

  async function changePassword(e: FormEvent) {
    e.preventDefault()
    if (!mustReset && !p0) return toast('Enter your current password.', 'error')
    if (p1.length < 8) return toast('Password must be at least 8 characters.', 'error')
    if (p1 !== p2) return toast('Passwords do not match.', 'error')
    setBusy(true)
    const error = await changeOwnPassword(p1, mustReset ? undefined : p0)
    if (error) {
      setBusy(false)
      toast(error, 'error')
      return
    }
    setP0('')
    setP1('')
    setP2('')
    toast(mustReset ? 'Password set — the app is open.' : 'Password updated.', 'success')
    await refreshMe()   // /me now says must_reset=false: the shell lets every tab through again
    setBusy(false)
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link)
      toast('Link copied', 'success')
    } catch {
      toast('Could not copy', 'error')
    }
  }

  const row = 'flex items-center gap-3 px-4 py-3.5 text-[14px] font-semibold hover:bg-muted'

  return (
    <div className="mx-auto max-w-3xl px-4 py-4 lg:px-8 lg:py-8">
      <h1 className="font-display text-[22px] font-bold leading-tight tracking-tight lg:text-[28px]">My account</h1>

      <section className="mt-4 rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center gap-3">
          {sm?.photo_url ? <img src={sm.photo_url} alt="" width={56} height={56} className="h-14 w-14 rounded-full object-cover" /> : <span className="grid h-14 w-14 place-items-center rounded-full bg-accent font-display text-[18px] font-bold text-accent-foreground">{initials(name)}</span>}
          <div className="min-w-0">
            <div className="truncate font-display text-[18px] font-bold leading-tight">{name}</div>
            <div className="truncate text-[12.5px] text-muted-foreground">{me?.email}</div>
            <div className="mt-1 text-[12px] text-muted-foreground">{sm?.title || 'YQ sales representative'}{sm?.referral_code ? ` · /${sm.referral_code}` : ''}</div>
          </div>
        </div>
        {focus?.revenue_90d_bhd ? (
          <div className="mt-4 rounded-xl bg-muted px-3 py-2.5 text-[12.5px]">
            <span className="text-muted-foreground">90-day accessories sales{focus.basis === 'net_ex_vat' ? ' (ex-VAT)' : ''} </span>
            <b className="tabular-nums">{bhd3(focus.revenue_90d_bhd)}</b>
          </div>
        ) : null}
        {focus?.error ? (
          <div role="status" className="mt-2 rounded-xl bg-muted px-3 py-2.5 text-[12.5px] text-muted-foreground">{focus.error}</div>
        ) : null}
        {focus?.target ? (
          <div className="mt-2 rounded-xl bg-muted px-3 py-2.5 text-[12.5px]">
            <span className="text-muted-foreground">{monthName(focus.target.month)} kickback · </span>
            <b className="tabular-nums">{bhd3(focus.target.mtd_bhd)}</b>
            <span className="text-muted-foreground"> sold{focus.target.basis === 'net_ex_vat' ? ' ex-VAT' : ''} · </span>
            <b>{focus.target.tier_reached > 0 ? `Tier ${focus.target.tier_reached} (${Math.round(focus.target.kickback_pct * 100)}%)` : 'below Tier 1'}</b>
            {focus.target.next_tier ? (
              <span className="text-muted-foreground"> · {bhd3(focus.target.next_tier.gap_bhd)} to Tier {focus.target.next_tier.n}</span>
            ) : null}
            <div className="mt-1 text-[11px] text-muted-foreground">
              Tiers {focus.target.tiers.map((t) => `BHD ${t.bhd.toLocaleString('en-US')} (${Math.round(t.pct * 100)}%)`).join(' · ')}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              Estimated kickback {bhd3(focus.target.kickback_bhd)} · final after approval ·{' '}
              {focus.target.returns_deducted ? 'returns deducted' : 'returns not yet deducted'}
            </div>
          </div>
        ) : null}
        {/* R3a: the money on record — the latest approved / paid statement, then the open draft */}
        {meQ.data?.last_closed ? (
          <div className="mt-2 rounded-xl border border-primary/30 bg-card px-3 py-2.5 text-[12.5px]" aria-label="Last closed month">
            <span className="text-muted-foreground">{monthName(meQ.data.last_closed.period)} final · </span>
            <b>{meQ.data.last_closed.tier_reached > 0 ? `Tier ${meQ.data.last_closed.tier_reached}` : 'below Tier 1'}</b>
            <span className="text-muted-foreground"> · </span>
            <b className="tabular-nums">{bhdStr(meQ.data.last_closed.kickback_bhd)}</b>
            <span className="text-muted-foreground">
              {' '}· {meQ.data.last_closed.status === 'paid'
                ? `paid${meQ.data.last_closed.paid_at ? ` ${dayLabel(meQ.data.last_closed.paid_at)}` : ''}`
                : `approved${meQ.data.last_closed.approved_at ? ` ${dayLabel(meQ.data.last_closed.approved_at)}` : ''}, payment to follow`}
            </span>
            <div className="mt-1 text-[11px] text-muted-foreground">
              On {bhdStr(meQ.data.last_closed.sales_bhd)} sold{meQ.data.last_closed.basis === 'net_ex_vat' ? ' ex-VAT' : ''}
              {meQ.data.last_closed.data_through ? ` · sales data to ${dayLabel(meQ.data.last_closed.data_through)}` : ''}
              {meQ.data.last_closed.returns_bhd == null ? ' · returns not deducted' : ` · returns ${bhdStr(meQ.data.last_closed.returns_bhd)} deducted`}
            </div>
          </div>
        ) : null}
        {meQ.data?.draft ? (
          <div className="mt-2 text-[11.5px] text-muted-foreground">
            {monthName(meQ.data.draft.period)} draft · {meQ.data.draft.tier_reached > 0 ? `Tier ${meQ.data.draft.tier_reached}` : 'below Tier 1'} · {bhdStr(meQ.data.draft.kickback_bhd)} · awaiting the office's approval
          </div>
        ) : null}
      </section>

      {/* link */}
      <section className="mt-4 overflow-hidden rounded-2xl border border-border bg-card">
        <div className="px-4 pt-4">
          <h2 className="font-display text-[15px] font-bold">My storefront link</h2>
          <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">{link.replace(/^https?:\/\//, '') || (meQ.data?.hint ?? '…')}</p>
        </div>
        <div className="mt-3 grid grid-cols-3 divide-x divide-border border-t border-border">
          <button type="button" onClick={copy} className="inline-flex h-12 items-center justify-center gap-2 text-[13px] font-semibold hover:bg-muted">
            <Copy size={15} aria-hidden="true" /> Copy
          </button>
          <button type="button" onClick={() => setQrOpen((v) => !v)} className="inline-flex h-12 items-center justify-center gap-2 text-[13px] font-semibold hover:bg-muted">
            <QrCode size={15} aria-hidden="true" /> QR
          </button>
          <a href={link || '#'} target="_blank" rel="noreferrer" className="inline-flex h-12 items-center justify-center gap-2 text-[13px] font-semibold hover:bg-muted">
            <ExternalLink size={15} aria-hidden="true" /> Open
          </a>
        </div>
        {qrOpen && (
          <div className="grid place-items-center border-t border-border bg-white p-4">
            {qr ? <img src={qr} alt="QR code for my link" width={240} height={240} className="h-60 w-60" /> : <Loader2 size={18} className="my-24 animate-spin text-muted-foreground" />}
          </div>
        )}
      </section>

      {sm?.whatsapp || sm?.phone ? (
        <p className="mt-2 px-1 text-[11.5px] text-muted-foreground">
          <MessageCircle size={11} className="inline" aria-hidden="true" /> Merchants message you on {sm.whatsapp || sm.phone}. Ask the office to change it.
        </p>
      ) : null}

      {/* more tools */}
      {more.length > 0 && (
        <section className="mt-4 overflow-hidden rounded-2xl border border-border bg-card">
          <h2 className="px-4 pt-4 font-display text-[15px] font-bold">More tools</h2>
          <ul className="mt-2 divide-y divide-border border-t border-border">
            {more.map((n) => (
              <li key={n.to}>
                <Link to={n.to} className={row}>
                  <n.icon size={18} className="text-primary" aria-hidden="true" />
                  <span className="flex-1">{n.label}</span>
                  <ChevronRight size={16} className="text-muted-foreground" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* password */}
      <section id="password" className={cn('mt-4 rounded-2xl border border-border bg-card p-4', mustReset && 'border-amber-300 ring-2 ring-amber-200')}>
        <h2 className="flex items-center gap-2 font-display text-[15px] font-bold">
          <KeyRound size={16} className="text-primary" aria-hidden="true" /> {mustReset ? 'Set your own password' : 'Change password'}
        </h2>
        {mustReset && (
          <p className="mt-1.5 text-[12.5px] leading-snug text-muted-foreground">
            Set your own password to continue — you signed in with a temporary one, and the rest of the app opens as soon as you choose yours.
          </p>
        )}
        <form onSubmit={changePassword} className="mt-3 grid gap-2 sm:max-w-sm">
          {!mustReset && (
            <input type="password" autoComplete="current-password" value={p0} onChange={(e) => setP0(e.target.value)} placeholder="Current password" className="h-12 rounded-xl border border-border bg-card px-3.5 text-[16px] outline-none focus:border-primary" />
          )}
          <input type="password" autoComplete="new-password" value={p1} onChange={(e) => setP1(e.target.value)} placeholder="New password" className="h-12 rounded-xl border border-border bg-card px-3.5 text-[16px] outline-none focus:border-primary" />
          <input type="password" autoComplete="new-password" value={p2} onChange={(e) => setP2(e.target.value)} placeholder="Confirm new password" className="h-12 rounded-xl border border-border bg-card px-3.5 text-[16px] outline-none focus:border-primary" />
          <button type="submit" disabled={busy} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-primary text-[13px] font-semibold text-primary-foreground disabled:opacity-50">
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} {mustReset ? 'Set password' : 'Update password'}
          </button>
        </form>
      </section>

      <button
        type="button"
        onClick={async () => {
          setSigningOut(true)
          await signOut()
          setSigningOut(false)
        }}
        className={cn('mt-4 inline-flex h-12 w-full items-center justify-center gap-2 rounded-2xl border border-border bg-card text-[14px] font-semibold text-destructive hover:bg-muted')}
      >
        {signingOut ? <Loader2 size={16} className="animate-spin" /> : <LogOut size={16} />} Sign out
      </button>
    </div>
  )
}
