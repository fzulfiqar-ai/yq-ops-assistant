import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import {
  Landmark, Boxes, Percent, TrendingUp, Megaphone, HeartPulse, Wallet, ShieldAlert,
  Hourglass, Award, Play, Mail, Check, Loader2, ChevronRight, Clock, ClipboardList,
  Truck, Siren, Flame, Sparkles, GitCompareArrows, Globe, Gauge, Layers3, Tags, Undo2,
  CreditCard, Send, Tag, Banknote, Workflow, ClipboardCheck, Combine, Star, Radar, Target, Search, type LucideIcon,
} from 'lucide-react'
import { apiGet, apiPost } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { useToast } from '@/components/Toast'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface AgentInfo { name: string; description: string; department?: string }
type AgentResult = Record<string, unknown> & { summary?: string; generated_at?: string }

const ICONS: Record<string, LucideIcon> = {
  collections: Landmark, inventory: Boxes, margin: Percent, sales_insights: TrendingUp,
  sales_push: Megaphone, customer_health: HeartPulse, cashflow: Wallet, risk_watch: ShieldAlert,
  inventory_aging: Hourglass, salesman_performance: Award, purchase_insights: Truck,
  trend: Flame, marketing: Sparkles, catalog_watch: GitCompareArrows, vendor_sourcing: Globe,
  salesman_stock_recon: Siren, demand_forecast: Gauge, abc_xyz: Layers3,
  deadstock_liquidation: Tags, winback: Undo2, credit_exposure: CreditCard,
  pricing_optimization: Tag, working_capital: Banknote, reorder_proposal: ClipboardCheck,
  procurement_status: Workflow, cross_sell: Combine, vendor_scorecard: Star, trend_radar: Radar,
  lead_gen: Target, research_scout: Search, price_drift: Gauge, returns_investigator: Undo2,
}

// CEO → departments → agents. Order + role label per department for the org map.
const DEPARTMENTS: { key: string; role: string; icon: LucideIcon }[] = [
  { key: 'Finance', role: 'CFO', icon: Wallet },
  { key: 'Supply', role: 'COO', icon: Boxes },
  { key: 'Sales & Growth', role: 'CRO', icon: TrendingUp },
  { key: 'Risk', role: 'Risk & Compliance', icon: ShieldAlert },
  { key: 'Operations', role: 'Ops', icon: Sparkles },
]

function relTime(iso?: string) {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const h = Math.floor(ms / 3.6e6)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
const META = new Set(['agent', 'description', 'generated_at', 'summary', 'count', 'email'])
const DRAFTS = new Set(['inventory', 'margin', 'anomaly', 'collections'])

const prettyTitle = (name: string) => name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
function fmtVal(k: string, v: unknown) {
  if (v == null) return '—'
  if (typeof v === 'number') return /bhd/i.test(k) ? `BHD ${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}` : v.toLocaleString('en-US')
  return String(v)
}
function firstList(d: AgentResult): { key: string; rows: Record<string, unknown>[] } | null {
  for (const [k, v] of Object.entries(d)) {
    if (!META.has(k) && Array.isArray(v) && v.length && typeof v[0] === 'object') {
      return { key: k, rows: v as Record<string, unknown>[] }
    }
  }
  return null
}

interface RunState { loading?: boolean; data?: AgentResult; error?: string; emailing?: boolean; emailed?: boolean; drafting?: boolean }

export default function Agents() {
  const { data: agents, isLoading } = useQuery({ queryKey: ['agents'], queryFn: () => apiGet<AgentInfo[]>('/agents') })
  const { data: schedules } = useQuery({ queryKey: ['schedules'], queryFn: () => apiGet<Record<string, string>>('/schedules') })
  const [state, setState] = useState<Record<string, RunState>>({})
  const [schedLocal, setSchedLocal] = useState<Record<string, string>>({})
  const [briefing, setBriefing] = useState(false)
  const toast = useToast()
  const set = (name: string, patch: RunState) => setState((s) => ({ ...s, [name]: { ...s[name], ...patch } }))
  const sched = { ...(schedules || {}), ...schedLocal }

  async function setSchedule(name: string, cadence: string) {
    setSchedLocal((s) => ({ ...s, [name]: cadence }))
    try { await apiPost(`/schedules/${name}`, { cadence }); toast(`${prettyTitle(name)} — ${cadence === 'off' ? 'schedule off' : `auto-runs ${cadence}`}.`, 'success') }
    catch { toast('Could not update the schedule.', 'error') }
  }

  async function run(name: string) {
    set(name, { loading: true, error: undefined })
    try { set(name, { loading: false, data: await apiGet<AgentResult>(`/agents/${name}`) }) }
    catch { set(name, { loading: false, error: 'Failed to run this agent.' }) }
  }
  async function email(name: string) {
    set(name, { emailing: true })
    try {
      const r = await apiGet<AgentResult & { email?: { emailed?: boolean; reason?: string } }>(`/agents/${name}?email=1`)
      const sent = !!r.email?.emailed
      set(name, { emailing: false, emailed: sent })
      if (sent) {
        toast(`${prettyTitle(name)} briefing emailed to you.`, 'success')
        setTimeout(() => set(name, { emailed: false }), 3000)
      } else {
        toast(`Email not sent — ${r.email?.reason || 'email isn’t configured yet'}.`, 'error')
      }
    } catch { set(name, { emailing: false }); toast('Could not send the email. Please try again.', 'error') }
  }
  async function draft(name: string) {
    set(name, { drafting: true })
    try {
      const r = await apiPost<{ drafted?: number; count?: number; skipped?: number; reason?: string }>(`/agents/${name}/draft-actions`)
      set(name, { drafting: false })
      if (name === 'collections') toast(`${r.count ?? 0} bilingual reminders drafted — ready to send.`, 'success')
      else if (r.reason) toast(r.reason, 'info')
      else toast(`${r.drafted ?? 0} action(s) sent for approval${r.skipped ? ` · ${r.skipped} already pending` : ''}.`, 'success')
    } catch { set(name, { drafting: false }); toast('Could not draft actions.', 'error') }
  }
  async function runBriefing() {
    setBriefing(true)
    try { await apiGet('/escalation/brief'); toast('Morning briefing sent to your email (and Telegram if configured).', 'success') }
    catch { toast('Could not run the briefing.', 'error') }
    finally { setBriefing(false) }
  }

  function card(a: AgentInfo) {
    const Icon = ICONS[a.name] || ShieldAlert
    const st = state[a.name] || {}
    const list = st.data ? firstList(st.data) : null
    const attention = !!st.data?.summary && /\b([1-9]\d*)\b/.test(String(st.data.summary)) && !/^no\b|cleanly|0 /i.test(String(st.data.summary))
    return (
      <Card key={a.name} className="overflow-hidden">
        <div className="flex items-start gap-3.5 p-5">
          <div className="relative grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent text-accent-foreground">
            <Icon size={20} />
            <span className={cn('absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-card',
              st.loading ? 'animate-pulse bg-amber-400' : st.data ? (attention ? 'bg-amber-500' : 'bg-emerald-500') : 'bg-border')} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-display text-[15px] font-semibold">{prettyTitle(a.name)}</div>
            <div className="mt-0.5 text-[13px] text-muted-foreground">{a.description}</div>
          </div>
          <select value={sched[a.name] || 'off'} onChange={(e) => setSchedule(a.name, e.target.value)}
            title="Auto-run on a schedule and email you (08:00 Bahrain)"
            className="shrink-0 rounded-lg border bg-background px-2 py-1.5 text-[12px] text-muted-foreground outline-none transition hover:text-foreground">
            <option value="off">⏱ Off</option>
            <option value="daily">Daily 8AM</option>
            <option value="weekly">Weekly Mon</option>
          </select>
          <button onClick={() => run(a.name)} disabled={st.loading}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-primary-foreground shadow-soft transition hover:shadow-lift disabled:opacity-50">
            {st.loading ? <Loader2 className="animate-spin" size={14} /> : <Play size={14} />} Run
          </button>
        </div>

        <AnimatePresence>
          {st.error && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="border-t bg-destructive/5 px-5 py-3 text-sm text-destructive">{st.error}</motion.div>
          )}
          {st.data && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }} className="border-t">
              <div className="bg-accent/30 px-5 py-3">
                <p className="text-sm font-medium">{st.data.summary}</p>
                {st.data.generated_at && (
                  <div className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground"><Clock size={11} /> Ran {relTime(st.data.generated_at)}</div>
                )}
                {(() => {
                  const ch = st.data.changes as { first_run?: boolean; metric_deltas?: Record<string, number>; new_items?: string[]; resolved_items?: string[] } | undefined
                  if (!ch || ch.first_run) return null
                  const parts: string[] = []
                  Object.entries(ch.metric_deltas || {}).forEach(([k, v]) => { if (v) parts.push(`${k.replace(/_/g, ' ')} ${v > 0 ? '+' : ''}${v.toLocaleString()}`) })
                  if (ch.new_items?.length) parts.push(`${ch.new_items.length} new`)
                  if (ch.resolved_items?.length) parts.push(`${ch.resolved_items.length} resolved`)
                  return <div className="mt-1 text-[11px] font-medium text-primary">vs last run: {parts.length ? parts.join(' · ') : 'no change'}</div>
                })()}
              </div>
              {list && (
                <div className="px-5 py-3">
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{prettyTitle(list.key)}</div>
                  <ul className="space-y-1">
                    {list.rows.slice(0, 6).map((row, i) => {
                      const cols = Object.keys(row).slice(0, 3)
                      return (
                        <motion.li key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.06 }}
                          className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-accent/40">
                          <ChevronRight size={13} className="shrink-0 text-primary" />
                          <span className="truncate font-medium">{fmtVal(cols[0], row[cols[0]])}</span>
                          <span className="ml-auto flex shrink-0 gap-3 text-muted-foreground">{cols.slice(1).map((c) => <span key={c}>{fmtVal(c, row[c])}</span>)}</span>
                        </motion.li>
                      )
                    })}
                  </ul>
                  {list.rows.length > 6 && <div className="mt-1 px-2 text-xs text-muted-foreground">… and {list.rows.length - 6} more</div>}
                </div>
              )}
              <div className="flex items-center justify-between border-t px-5 py-2.5">
                {DRAFTS.has(a.name) ? (
                  <button onClick={() => draft(a.name)} disabled={st.drafting}
                    className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold text-primary transition hover:bg-accent disabled:opacity-50"
                    title="Draft actions/reminders for human approval">
                    {st.drafting ? <Loader2 className="animate-spin" size={14} /> : <ClipboardList size={14} />}
                    {a.name === 'collections' ? 'Draft reminders' : 'Draft actions'}
                  </button>
                ) : <span />}
                <button onClick={() => email(a.name)} disabled={st.emailing}
                  className={cn('flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold transition',
                    st.emailed ? 'text-emerald-600' : 'text-muted-foreground hover:bg-accent hover:text-foreground')}>
                  {st.emailing ? <Loader2 className="animate-spin" size={14} /> : st.emailed ? <Check size={14} /> : <Mail size={14} />}
                  {st.emailed ? 'Emailed' : 'Email me this'}
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    )
  }

  return (
    <div>
      <PageHeader title="The AI Team" subtitle="A chief-of-staff over four departments — run any specialist, or send the morning briefing." />

      {/* CEO / chief of staff */}
      <Card className="mb-6 overflow-hidden">
        <div className="flex flex-wrap items-center gap-4 p-5">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-lift"><Sparkles size={22} /></div>
          <div className="min-w-0 flex-1">
            <div className="font-display text-base font-semibold">Chief of Staff</div>
            <div className="text-[13px] text-muted-foreground">
              The orchestrator — routes your questions to {agents?.length ?? '…'} specialists across {DEPARTMENTS.filter((d) => (agents || []).some((a) => (a.department || 'Operations') === d.key)).length} departments and synthesises one answer.
            </div>
          </div>
          <button onClick={runBriefing} disabled={briefing}
            className="flex shrink-0 items-center gap-2 rounded-lg bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground shadow-soft transition hover:shadow-lift disabled:opacity-50">
            {briefing ? <Loader2 className="animate-spin" size={15} /> : <Send size={15} />} Run morning briefing
          </button>
        </div>
      </Card>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}</div>
      ) : (
        DEPARTMENTS.map((dept) => {
          const members = (agents || []).filter((a) => (a.department || 'Operations') === dept.key)
          if (!members.length) return null
          const DeptIcon = dept.icon
          return (
            <section key={dept.key} className="mb-8">
              <div className="mb-3 flex items-center gap-2.5">
                <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-accent-foreground"><DeptIcon size={16} /></div>
                <div className="font-display text-sm font-semibold">
                  <span className="text-muted-foreground">{dept.role}</span> · {dept.key}
                </div>
                <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground">{members.length}</span>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{members.map((a) => card(a))}</div>
            </section>
          )
        })
      )}
    </div>
  )
}
