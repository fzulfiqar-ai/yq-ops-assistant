import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { UserPlus, Mail, KeyRound, Trash2, Check, Loader2, Copy, ShieldCheck, User as UserIcon } from 'lucide-react'
import { apiGet, apiPost, apiPatch, apiDelete, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

const FEATURES = ['Dashboard', 'AI Agents', 'AI Assistant', 'Inventory', 'Sales', 'Margins', 'Receivables']

interface Member { email: string; role: string; features: string[]; status: string; full_name?: string }
interface Invite { email: string; role: string; full_name?: string; expires_at?: string }
interface TeamData { users: Member[]; invites: Invite[] }

function FeatureChips({ selected, onToggle }: { selected: string[]; onToggle: (f: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {FEATURES.map((f) => {
        const on = selected.includes(f)
        return (
          <button
            key={f}
            type="button"
            onClick={() => onToggle(f)}
            className={cn(
              'rounded-full border px-3 py-1 text-[13px] transition',
              on ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40',
            )}
          >
            {f}
          </button>
        )
      })}
    </div>
  )
}

export default function Team() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['team'],
    queryFn: () => apiGet<TeamData>('/team'),
    retry: false,
  })

  // invite form state
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'member' | 'admin'>('member')
  const [features, setFeatures] = useState<string[]>(['Dashboard'])
  const [method, setMethod] = useState<'temp' | 'email'>('temp')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ kind: 'temp' | 'email' | 'err'; msg: string } | null>(null)

  const refetch = () => qc.invalidateQueries({ queryKey: ['team'] })

  async function invite() {
    if (!email.includes('@')) {
      setResult({ kind: 'err', msg: 'Enter a valid email.' })
      return
    }
    if (role === 'member' && features.length === 0) {
      setResult({ kind: 'err', msg: 'Grant at least one feature, or make them an admin.' })
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const r = await apiPost<{ mode: string; temp_password?: string; link?: string; email?: { emailed?: boolean; reason?: string } }>(
        '/team/invite',
        { email, full_name: name, role, features, method },
      )
      if (r.mode === 'temp') {
        setResult({ kind: 'temp', msg: r.temp_password || '' })
      } else if (r.email?.emailed) {
        setResult({ kind: 'email', msg: `Invite emailed to ${email}.` })
      } else {
        setResult({ kind: 'email', msg: `Invite created. Email not sent (${r.email?.reason || 'unknown'}). Link: ${r.link}` })
      }
      setName('')
      setEmail('')
      refetch()
    } catch (e) {
      setResult({ kind: 'err', msg: e instanceof ApiError ? e.body.slice(0, 160) : 'Could not create the invite.' })
    } finally {
      setBusy(false)
    }
  }

  if (error) {
    const needsMigration = error instanceof ApiError && error.status >= 500
    return (
      <div>
        <PageHeader title="Team & Access" subtitle="Invite teammates and control what each person can see" />
        <Card className="p-6 text-sm">
          {needsMigration ? (
            <>
              <p className="font-semibold">One-time setup needed</p>
              <p className="mt-1 text-muted-foreground">
                Run <code className="rounded bg-muted px-1.5 py-0.5">scripts/team_management.sql</code> in Supabase to enable team
                management (it adds the <code>features</code> column + <code>app_invites</code> table), then reload.
              </p>
            </>
          ) : (
            <p className="text-muted-foreground">Couldn't load the team. {String(error)}</p>
          )}
        </Card>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Team & Access" subtitle="Invite teammates and control what each person can see" />

      {/* Invite */}
      <Card className="mb-5 p-5">
        <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
          <UserPlus size={18} className="text-primary" /> Invite a team member
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Input placeholder="Full name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input placeholder="email@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">Role:</span>
          {(['member', 'admin'] as const).map((r) => (
            <button
              key={r}
              onClick={() => setRole(r)}
              className={cn('rounded-lg border px-3 py-1.5 text-[13px] font-medium capitalize transition', role === r ? 'border-primary bg-accent' : 'border-border')}
            >
              {r}
            </button>
          ))}
        </div>
        {role === 'member' && (
          <div className="mt-3">
            <div className="mb-2 text-sm text-muted-foreground">Feature access</div>
            <FeatureChips selected={features} onToggle={(f) => setFeatures((s) => (s.includes(f) ? s.filter((x) => x !== f) : [...s, f]))} />
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">Onboarding:</span>
          <button onClick={() => setMethod('temp')} className={cn('flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] transition', method === 'temp' ? 'border-primary bg-accent' : 'border-border')}>
            <KeyRound size={14} /> Temp password
          </button>
          <button onClick={() => setMethod('email')} className={cn('flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] transition', method === 'email' ? 'border-primary bg-accent' : 'border-border')}>
            <Mail size={14} /> Email invite
          </button>
          <Button className="ml-auto" onClick={invite} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" size={16} /> : <UserPlus size={16} />}
            Create
          </Button>
        </div>

        {result && (
          <div
            className={cn(
              'mt-4 rounded-xl border p-3 text-sm',
              result.kind === 'err' ? 'border-destructive/30 bg-destructive/5 text-destructive' : 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300',
            )}
          >
            {result.kind === 'temp' ? (
              <div>
                <div className="font-semibold">Account created — share this temporary password securely:</div>
                <div className="mt-2 flex items-center gap-2">
                  <code className="rounded-lg bg-background px-3 py-1.5 font-mono text-base">{result.msg}</code>
                  <button onClick={() => navigator.clipboard?.writeText(result.msg)} className="rounded-lg border p-1.5 hover:bg-accent" title="Copy">
                    <Copy size={14} />
                  </button>
                </div>
                <div className="mt-1 text-xs opacity-80">They'll set their own password on first login.</div>
              </div>
            ) : (
              <div className="break-words">{result.msg}</div>
            )}
          </div>
        )}
      </Card>

      {/* Members */}
      <Card className="p-5">
        <div className="mb-4 font-display text-base font-semibold">Team members</div>
        {isLoading ? (
          <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
        ) : (
          <div className="space-y-2">
            {(data?.users || []).map((u) => (
              <MemberRow key={u.email} member={u} isSelf={u.email === me?.email} onChanged={refetch} />
            ))}
            {(data?.invites || []).map((inv) => (
              <div key={inv.email} className="flex items-center gap-3 rounded-xl border border-dashed px-4 py-3 text-sm">
                <Mail size={16} className="text-muted-foreground" />
                <span className="font-medium">{inv.email}</span>
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] uppercase text-muted-foreground">pending · {inv.role}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

function MemberRow({ member, isSelf, onChanged }: { member: Member; isSelf: boolean; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [role, setRole] = useState(member.role)
  const [features, setFeatures] = useState<string[]>(member.features || [])
  const [status, setStatus] = useState(member.status || 'active')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      await apiPatch(`/team/${encodeURIComponent(member.email)}`, { role, features: role === 'admin' ? FEATURES : features, status })
      setOpen(false)
      onChanged()
    } finally {
      setBusy(false)
    }
  }
  async function remove() {
    if (!confirm(`Remove ${member.email}?`)) return
    setBusy(true)
    try {
      await apiDelete(`/team/${encodeURIComponent(member.email)}`)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border">
      <div className="flex items-center gap-3 px-4 py-3">
        <div className={cn('grid h-9 w-9 place-items-center rounded-full', member.role === 'admin' ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
          {member.role === 'admin' ? <ShieldCheck size={16} /> : <UserIcon size={16} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">
            {member.full_name || member.email.split('@')[0]} {isSelf && <span className="text-xs font-normal text-muted-foreground">(you)</span>}
          </div>
          <div className="truncate text-xs text-muted-foreground">{member.email}</div>
        </div>
        <span className={cn('rounded-full px-2 py-0.5 text-[11px] uppercase', member.status === 'active' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' : 'bg-muted text-muted-foreground')}>
          {member.status}
        </span>
        <span className="hidden text-[11px] uppercase tracking-wide text-muted-foreground sm:block">{member.role}</span>
        <Button variant="outline" size="sm" onClick={() => setOpen((o) => !o)}>Edit</Button>
      </div>
      {open && (
        <div className="space-y-3 border-t bg-secondary/30 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">Role:</span>
            {(['member', 'admin'] as const).map((r) => (
              <button key={r} onClick={() => setRole(r)} disabled={isSelf} className={cn('rounded-lg border px-3 py-1 text-[13px] capitalize', role === r ? 'border-primary bg-accent' : 'border-border', isSelf && 'opacity-50')}>{r}</button>
            ))}
            <span className="ml-3 text-sm text-muted-foreground">Status:</span>
            {(['active', 'disabled'] as const).map((s) => (
              <button key={s} onClick={() => setStatus(s)} disabled={isSelf} className={cn('rounded-lg border px-3 py-1 text-[13px] capitalize', status === s ? 'border-primary bg-accent' : 'border-border', isSelf && 'opacity-50')}>{s}</button>
            ))}
          </div>
          {role === 'member' && (
            <FeatureChips selected={features} onToggle={(f) => setFeatures((s) => (s.includes(f) ? s.filter((x) => x !== f) : [...s, f]))} />
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={save} disabled={busy}>{busy ? <Loader2 className="animate-spin" size={14} /> : <Check size={14} />} Save</Button>
            {!isSelf && (
              <Button size="sm" variant="destructive" onClick={remove} disabled={busy}><Trash2 size={14} /> Remove</Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
