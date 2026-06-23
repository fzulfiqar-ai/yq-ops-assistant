import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import {
  Landmark, Boxes, Percent, TrendingUp, Megaphone, HeartPulse, Wallet, ShieldAlert,
  Hourglass, Award, Play, Mail, Check, Loader2, ChevronRight, Clock, type LucideIcon,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface AgentInfo { name: string; description: string }
type AgentResult = Record<string, unknown> & { summary?: string; generated_at?: string }

const ICONS: Record<string, LucideIcon> = {
  collections: Landmark,
  inventory: Boxes,
  margin: Percent,
  sales_insights: TrendingUp,
  sales_push: Megaphone,
  customer_health: HeartPulse,
  cashflow: Wallet,
  anomaly: ShieldAlert,
  inventory_aging: Hourglass,
  salesman_performance: Award,
}

function relTime(iso?: string) {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const h = Math.floor(ms / 3.6e6)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
const META = new Set(['agent', 'description', 'generated_at', 'summary', 'count', 'email'])

function prettyTitle(name: string) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
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

interface RunState { loading?: boolean; data?: AgentResult; error?: string; emailing?: boolean; emailed?: boolean }

export default function Agents() {
  const { data: agents, isLoading } = useQuery({ queryKey: ['agents'], queryFn: () => apiGet<AgentInfo[]>('/agents') })
  const [state, setState] = useState<Record<string, RunState>>({})
  const set = (name: string, patch: RunState) => setState((s) => ({ ...s, [name]: { ...s[name], ...patch } }))

  async function run(name: string) {
    set(name, { loading: true, error: undefined })
    try {
      set(name, { loading: false, data: await apiGet<AgentResult>(`/agents/${name}`) })
    } catch {
      set(name, { loading: false, error: 'Failed to run this agent.' })
    }
  }
  async function email(name: string) {
    set(name, { emailing: true })
    try {
      await apiGet(`/agents/${name}?email=1`)
      set(name, { emailing: false, emailed: true })
      setTimeout(() => set(name, { emailed: false }), 3000)
    } catch {
      set(name, { emailing: false })
    }
  }

  return (
    <div>
      <PageHeader title="AI Agents" subtitle="Your autonomous team — run any agent on demand, or let the schedules work." />

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {(agents || []).map((a) => {
            const Icon = ICONS[a.name] || ShieldAlert
            const st = state[a.name] || {}
            const list = st.data ? firstList(st.data) : null
            return (
              <Card key={a.name} className="overflow-hidden">
                <div className="flex items-start gap-3.5 p-5">
                  <div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent text-accent-foreground">
                    <Icon size={20} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-display text-[15px] font-semibold">{prettyTitle(a.name)}</div>
                    <div className="mt-0.5 text-[13px] text-muted-foreground">{a.description}</div>
                  </div>
                  <button
                    onClick={() => run(a.name)}
                    disabled={st.loading}
                    className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-primary-foreground shadow-soft transition hover:shadow-lift disabled:opacity-50"
                  >
                    {st.loading ? <Loader2 className="animate-spin" size={14} /> : <Play size={14} />}
                    Run
                  </button>
                </div>

                <AnimatePresence>
                  {st.error && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="border-t bg-destructive/5 px-5 py-3 text-sm text-destructive">
                      {st.error}
                    </motion.div>
                  )}
                  {st.data && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                      className="border-t"
                    >
                      <div className="bg-accent/30 px-5 py-3">
                        <p className="text-sm font-medium">{st.data.summary}</p>
                        {st.data.generated_at && (
                          <div className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                            <Clock size={11} /> Ran {relTime(st.data.generated_at)}
                          </div>
                        )}
                      </div>
                      {list && (
                        <div className="px-5 py-3">
                          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                            {prettyTitle(list.key)}
                          </div>
                          <ul className="space-y-1">
                            {list.rows.slice(0, 6).map((row, i) => {
                              const cols = Object.keys(row).slice(0, 3)
                              return (
                                <motion.li
                                  key={i}
                                  initial={{ opacity: 0, x: -8 }}
                                  animate={{ opacity: 1, x: 0 }}
                                  transition={{ delay: i * 0.06 }}
                                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-accent/40"
                                >
                                  <ChevronRight size={13} className="shrink-0 text-primary" />
                                  <span className="truncate font-medium">{fmtVal(cols[0], row[cols[0]])}</span>
                                  <span className="ml-auto flex shrink-0 gap-3 text-muted-foreground">
                                    {cols.slice(1).map((c) => (
                                      <span key={c}>{fmtVal(c, row[c])}</span>
                                    ))}
                                  </span>
                                </motion.li>
                              )
                            })}
                          </ul>
                          {list.rows.length > 6 && (
                            <div className="mt-1 px-2 text-xs text-muted-foreground">… and {list.rows.length - 6} more</div>
                          )}
                        </div>
                      )}
                      <div className="flex items-center justify-end border-t px-5 py-2.5">
                        <button
                          onClick={() => email(a.name)}
                          disabled={st.emailing}
                          className={cn(
                            'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold transition',
                            st.emailed ? 'text-emerald-600' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                          )}
                        >
                          {st.emailing ? <Loader2 className="animate-spin" size={14} /> : st.emailed ? <Check size={14} /> : <Mail size={14} />}
                          {st.emailed ? 'Emailed' : 'Email me this'}
                        </button>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
