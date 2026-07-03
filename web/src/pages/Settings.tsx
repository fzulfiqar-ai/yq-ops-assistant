import { useState, type FormEvent } from 'react'
import { Moon, Sun, ShieldCheck, User as UserIcon, KeyRound, Check, Loader2, Calculator } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { useToast } from '@/components/Toast'
import { supabase } from '@/lib/supabase'
import { apiGet, apiSend } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type Costing = Record<string, number>

const COSTING_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: 'fx_rmb_usd', label: 'RMB per USD', hint: 'Order costing exchange leg' },
  { key: 'fx_usd_bhd', label: 'BHD per USD', hint: 'Order costing exchange leg' },
  { key: 'dealer_discount', label: 'Dealer discount', hint: '0.18 = net price is list ÷ 1.18' },
  { key: 'landing_vat_pct', label: 'Landing + VAT uplift', hint: '0.30 = 20% landing + 10% VAT' },
  { key: 'target_markup', label: 'Target markup', hint: '0.70 = sell at landed × 1.70' },
  { key: 'monthly_sales_target_bhd', label: 'Monthly sales target (BHD)', hint: '0 = no target set' },
]

function CostingCard() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['costing'], queryFn: () => apiGet<Costing>('/settings/costing') })
  const [draft, setDraft] = useState<Record<string, string>>({})
  const save = useMutation({
    mutationFn: () => {
      const changes: Costing = {}
      for (const [k, v] of Object.entries(draft)) {
        const n = parseFloat(v)
        if (!Number.isNaN(n) && n !== data?.[k]) changes[k] = n
      }
      return apiSend<Costing>('PUT', '/settings/costing', changes)
    },
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['costing'] }); toast('Business settings saved.', 'success') },
    onError: (e: Error) => toast(e.message, 'error'),
  })
  if (!data) return null
  return (
    <Card className="mb-4 p-6">
      <div className="mb-1 flex items-center gap-2 font-display text-base font-semibold">
        <Calculator size={18} className="text-primary" /> Business settings
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        The costing chain used across order verification, reorder proposals and pricing:
        RMB ÷ (1 + discount) ÷ RMB/USD × BHD/USD → base cost · × (1 + landing) → landed · × (1 + markup) → sell.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {COSTING_FIELDS.map((f) => (
          <label key={f.key} className="block">
            <span className="mb-1 block text-xs font-semibold text-muted-foreground">{f.label}</span>
            <Input inputMode="decimal" value={draft[f.key] ?? String(data[f.key] ?? '')}
              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
            <span className="mt-0.5 block text-[11px] text-muted-foreground">{f.hint}</span>
          </label>
        ))}
      </div>
      <Button className="mt-4" onClick={() => save.mutate()} disabled={save.isPending || Object.keys(draft).length === 0}>
        {save.isPending ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Save settings
      </Button>
    </Card>
  )
}

export default function Settings() {
  const { me } = useAuth()
  const { theme, toggle } = useTheme()
  const toast = useToast()
  const [p1, setP1] = useState('')
  const [p2, setP2] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function changePassword(e: FormEvent) {
    e.preventDefault()
    if (p1.length < 8) return setMsg({ ok: false, text: 'Password must be at least 8 characters.' })
    if (p1 !== p2) return setMsg({ ok: false, text: 'Passwords do not match.' })
    setBusy(true); setMsg(null)
    const { error } = await supabase.auth.updateUser({ password: p1 })
    setBusy(false)
    if (error) { setMsg({ ok: false, text: error.message }); toast(error.message, 'error') }
    else { setP1(''); setP2(''); setMsg(null); toast('Password updated successfully.', 'success') }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" subtitle="Your profile, appearance and security" />

      <Card className="mb-4 p-6">
        <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
          <UserIcon size={18} className="text-primary" /> Profile
        </div>
        <div className="flex items-center gap-4">
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-primary text-xl font-bold text-primary-foreground">
            {(me?.full_name || me?.email || 'U')[0].toUpperCase()}
          </div>
          <div>
            <div className="text-lg font-semibold">{me?.full_name || me?.email?.split('@')[0]}</div>
            <div className="text-sm text-muted-foreground">{me?.email}</div>
            <div className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-accent-foreground">
              <ShieldCheck size={12} /> {me?.role}
            </div>
          </div>
        </div>
        {me?.role !== 'admin' && (me?.features?.length ?? 0) > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {me!.features!.map((f) => (
              <span key={f} className="rounded-full border px-2.5 py-0.5 text-[12px] text-muted-foreground">{f}</span>
            ))}
          </div>
        )}
      </Card>

      {me?.role === 'admin' && <CostingCard />}

      <Card className="mb-4 p-6">
        <div className="mb-4 font-display text-base font-semibold">Appearance</div>
        <button onClick={toggle} className="flex items-center gap-3 rounded-xl border bg-card px-4 py-3 text-sm font-medium transition hover:border-primary/40">
          {theme === 'dark' ? <Moon size={18} className="text-primary" /> : <Sun size={18} className="text-amber-500" />}
          {theme === 'dark' ? 'Dark mode' : 'Light mode'}
          <span className="ml-auto text-xs text-muted-foreground">Click to switch</span>
        </button>
      </Card>

      <Card className="p-6">
        <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
          <KeyRound size={18} className="text-primary" /> Change password
        </div>
        <form onSubmit={changePassword} className="max-w-sm space-y-3">
          <Input type="password" placeholder="New password" value={p1} onChange={(e) => setP1(e.target.value)} />
          <Input type="password" placeholder="Confirm new password" value={p2} onChange={(e) => setP2(e.target.value)} />
          {msg && (
            <div className={cn('rounded-lg px-3 py-2 text-sm', msg.ok ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' : 'bg-destructive/10 text-destructive')}>
              {msg.text}
            </div>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Update password
          </Button>
        </form>
      </Card>
    </div>
  )
}
