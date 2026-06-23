import { useState, type FormEvent } from 'react'
import { Moon, Sun, ShieldCheck, User as UserIcon, KeyRound, Check, Loader2 } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { useToast } from '@/components/Toast'
import { supabase } from '@/lib/supabase'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

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
