import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Send, Mail, MessageCircle, Loader2, Check, X, Sparkles, Users, Megaphone,
  Clapperboard, BarChart3, ClipboardCopy, RefreshCw, Play, Search,
} from 'lucide-react'
import { apiGet, apiPost, apiSend } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { useToast } from '@/components/Toast'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

/** Marketing Studio — the 10k/month engine. Every outbound message and social post
 *  waits here for a human tap (owner decision): WhatsApp = one-tap wa.me links
 *  (assist mode), email = server-side send, social = approve → publish. */

interface QueueRow {
  id: number; target_name: string; target_type: string; source_agent: string
  channel: 'whatsapp' | 'email'; phone: string | null; email: string | null
  wa_link: string | null; message_en: string; message_ar: string | null
  reason: string | null; impact_bhd: number | null; status: string
}
interface SocialPost {
  id: number; campaign: string; item_code: string; kind: 'image' | 'video'
  template: string; caption_en: string; caption_ar: string | null
  media_url: string; platforms: string[]; status: string
}
interface Kpis {
  month: string; revenue_bhd: number; projected_bhd: number; target_bhd: number
  on_track: boolean; reactivated_30d: number
  coverage: { customers: number; with_phone: number; with_email: number; top50_covered: number }
  weekly_kpis: { week: string; touches: number; converted_targets: number; attributed_bhd: number }[]
  summary: string
}
interface Campaign {
  campaign: string; impact_bhd: number; channel: string
  message_en: string; message_ar: string; items: Record<string, unknown>[]
}

const TABS = [
  { id: 'send', label: 'Send Center', icon: Send },
  { id: 'contacts', label: 'Contacts', icon: Users },
  { id: 'campaigns', label: 'Campaigns', icon: Megaphone },
  { id: 'content', label: 'Content', icon: Clapperboard },
  { id: 'results', label: 'Results', icon: BarChart3 },
] as const

const AGENT_LABEL: Record<string, string> = {
  sales_outreach: 'Reorder nudge', winback: 'Win-back', sales_push: 'Clearance offer',
  lead_gen: 'New lead', manual: 'Manual',
}

function SendCenter() {
  const qc = useQueryClient()
  const toast = useToast()
  const [status, setStatus] = useState('draft')
  const [building, setBuilding] = useState(false)
  const { data: rows, isLoading } = useQuery({
    queryKey: ['outreach', status],
    queryFn: () => apiGet<QueueRow[]>(`/outreach/queue?status=${status}`),
  })

  async function build() {
    setBuilding(true)
    try {
      const r = await apiPost<{ summary: string }>('/outreach/build')
      toast(r.summary || 'Queue rebuilt.', 'success')
      qc.invalidateQueries({ queryKey: ['outreach'] })
    } catch { toast('Build failed — try again.', 'error') }
    finally { setBuilding(false) }
  }

  async function act(row: QueueRow, action: 'send' | 'dismiss') {
    try {
      if (action === 'dismiss') {
        await apiSend('PATCH', `/outreach/${row.id}/status`, { status: 'dismissed' })
        toast('Dismissed.', 'info')
      } else {
        if (row.channel === 'whatsapp' && row.wa_link) window.open(row.wa_link, '_blank')
        const r = await apiPost<{ ok: boolean; reason?: string }>(`/outreach/${row.id}/send`)
        if (!r.ok) { toast(r.reason || 'Send failed.', 'error'); return }
        toast(row.channel === 'email' ? 'Email sent ✓' : 'Logged — message opened in WhatsApp ✓', 'success')
      }
      qc.invalidateQueries({ queryKey: ['outreach'] })
    } catch { toast('Action failed.', 'error') }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {['draft', 'sent', 'all'].map((s) => (
          <button key={s} onClick={() => setStatus(s)}
            className={cn('rounded-full border px-3.5 py-1 text-xs font-medium capitalize',
              status === s ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'bg-white text-muted-foreground')}>
            {s === 'draft' ? 'Waiting' : s}
          </button>
        ))}
        <Button size="sm" variant="outline" className="ml-auto" onClick={build} disabled={building}>
          {building ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Build queue
        </Button>
      </div>
      {isLoading ? <Card className="p-8 text-center text-sm text-muted-foreground">Loading…</Card>
        : !(rows || []).length ? (
          <Card className="p-8 text-center text-sm text-muted-foreground">
            Nothing waiting. Tap <b>Build queue</b> to let the agents draft this week's
            reorder nudges, win-backs and lead openers.
          </Card>
        ) : (
          <div className="space-y-2.5">
            {(rows || []).map((r) => (
              <Card key={r.id} className="p-3.5">
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-display text-sm font-bold">{r.target_name}</span>
                      <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
                        {AGENT_LABEL[r.source_agent] || r.source_agent}
                      </span>
                      {r.impact_bhd ? (
                        <span className="text-[11px] text-muted-foreground">BHD {Number(r.impact_bhd).toLocaleString()} lifetime</span>
                      ) : null}
                    </div>
                    {r.reason && <div className="mt-0.5 text-[11px] text-muted-foreground">{r.reason}</div>}
                    <p className="mt-2 whitespace-pre-line rounded-lg bg-muted/60 p-2.5 text-[12.5px] leading-relaxed">
                      {r.message_en}
                    </p>
                  </div>
                  {status !== 'sent' && (
                    <div className="flex w-full flex-row gap-2 sm:w-auto sm:flex-col">
                      <Button size="sm" className="flex-1 bg-[#25D366] text-white hover:bg-[#1fb355] sm:flex-none"
                        disabled={r.channel !== 'whatsapp' && !r.email}
                        onClick={() => act(r, 'send')}>
                        {r.channel === 'whatsapp'
                          ? <><MessageCircle size={14} /> Send WhatsApp</>
                          : <><Mail size={14} /> Send email</>}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(r.message_en).then(() => { /* copied */ })}>
                        <ClipboardCopy size={14} />
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => act(r, 'dismiss')}><X size={14} /></Button>
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
    </div>
  )
}

function Contacts() {
  const qc = useQueryClient()
  const toast = useToast()
  const [paste, setPaste] = useState('')
  const [preview, setPreview] = useState<{ input: string; matched: string | null; phone: string; email: string; status: string }[] | null>(null)
  const [busy, setBusy] = useState(false)
  const { data: cov } = useQuery({ queryKey: ['coverage'], queryFn: () => apiGet<Kpis['coverage']>('/contacts/coverage') })

  const parsed = useMemo(() => paste.split('\n').map((l) => {
    const [name = '', phone = '', email = ''] = l.split(/[,\t;]/).map((s) => s.trim())
    return { customer_name: name, phone, email }
  }).filter((r) => r.customer_name && (r.phone || r.email)), [paste])

  async function run(commit: boolean) {
    setBusy(true)
    try {
      const r = await apiPost<{ rows: NonNullable<typeof preview>; matched: number; committed: number }>(
        '/contacts/import', { rows: parsed, commit })
      setPreview(r.rows)
      if (commit) {
        toast(`Saved ${r.committed} contacts ✓`, 'success')
        setPaste(''); setPreview(null)
        qc.invalidateQueries({ queryKey: ['coverage'] })
      }
    } catch { toast('Import failed (admin only).', 'error') }
    finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: 'Named customers', v: cov?.customers },
          { label: 'With phone', v: cov?.with_phone },
          { label: 'With email', v: cov?.with_email },
          { label: 'Top-50 covered', v: cov ? `${cov.top50_covered}/50` : undefined },
        ].map((s) => (
          <Card key={s.label} className="p-3.5">
            <div className="text-[11px] font-medium text-muted-foreground">{s.label}</div>
            <div className="font-display text-2xl font-extrabold text-[#6d28d9]">{s.v ?? '—'}</div>
          </Card>
        ))}
      </div>
      <Card className="p-4">
        <div className="mb-1 font-display text-sm font-semibold">Bulk import (paste from anywhere)</div>
        <p className="mb-2 text-[12px] text-muted-foreground">
          One customer per line: <code>Name, phone, email</code> — names are fuzzy-matched
          against your sales history. Phones you add here are never overwritten by the robot.
        </p>
        <textarea value={paste} onChange={(e) => setPaste(e.target.value)} rows={6}
          placeholder={'Phone Connect W.L.L, 3311 2233\nARAFA PHONES, 3900 1122, arafa@shop.bh'}
          className="w-full rounded-lg border bg-white p-2.5 font-mono text-[12px] outline-none focus:border-[#6d28d9]" />
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="outline" disabled={!parsed.length || busy} onClick={() => run(false)}>
            <Search size={14} /> Preview match ({parsed.length})
          </Button>
          <Button size="sm" disabled={!preview?.some((p) => p.status === 'matched') || busy} onClick={() => run(true)}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save matched
          </Button>
        </div>
        {preview && (
          <div className="mt-3 space-y-1">
            {preview.map((p, i) => (
              <div key={i} className={cn('flex flex-wrap items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12px]',
                p.status === 'matched' ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800')}>
                <span className="font-medium">{p.input}</span>
                <span>→ {p.matched || 'no match in sales history'}</span>
                <span className="ml-auto text-muted-foreground">{p.phone} {p.email}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
      <p className="text-[11px] text-muted-foreground">
        The <b>contact_enrich</b> agent also runs nightly, finding business numbers from
        public listings for your highest-value customers automatically.
      </p>
    </div>
  )
}

function Campaigns() {
  const toast = useToast()
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['marketing-campaigns'],
    queryFn: () => apiGet<{ campaigns: Campaign[]; catalog_link: string }>('/agents/marketing'),
    staleTime: 10 * 60 * 1000,
  })
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => toast('Copied — paste into WhatsApp.', 'success'))
  return (
    <div className="space-y-3">
      <div className="flex items-center">
        <p className="text-[12px] text-muted-foreground">Three ready-to-broadcast campaigns, rebuilt from live stock & margin data.</p>
        <Button size="sm" variant="outline" className="ml-auto" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Refresh
        </Button>
      </div>
      {isLoading ? <Card className="p-8 text-center text-sm text-muted-foreground">The marketing agent is thinking…</Card>
        : (data?.campaigns || []).map((c) => (
          <Card key={c.campaign} className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Sparkles size={15} className="text-[#6d28d9]" />
              <span className="font-display text-sm font-bold">{c.campaign}</span>
              {c.impact_bhd ? <span className="text-[11px] text-muted-foreground">~BHD {Number(c.impact_bhd).toLocaleString()} at stake</span> : null}
              <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[10px]">{c.channel}</span>
            </div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {[{ label: 'English', t: c.message_en }, { label: 'العربية', t: c.message_ar }].map((m) => (
                <div key={m.label} className="rounded-lg bg-muted/60 p-2.5">
                  <div className="mb-1 flex items-center text-[10px] font-semibold text-muted-foreground">
                    {m.label}
                    <button className="ml-auto rounded p-1 hover:bg-white" onClick={() => copy(m.t)}><ClipboardCopy size={13} /></button>
                  </div>
                  <p className="whitespace-pre-line text-[12.5px] leading-relaxed" dir={m.label === 'العربية' ? 'rtl' : 'ltr'}>{m.t}</p>
                </div>
              ))}
            </div>
          </Card>
        ))}
    </div>
  )
}

function Content() {
  const qc = useQueryClient()
  const toast = useToast()
  const [generating, setGenerating] = useState(false)
  const [polling, setPolling] = useState(false)
  const [publishing, setPublishing] = useState<number | null>(null)
  const { data: posts } = useQuery({
    queryKey: ['social-posts'], queryFn: () => apiGet<SocialPost[]>('/social/posts'),
    // auto-refresh while Agnes videos are still rendering
    refetchInterval: (q) => ((q.state.data || []).some((p) => p.status === 'rendering') ? 15000 : false),
  })
  const { data: conf } = useQuery({ queryKey: ['social-config'], queryFn: () => apiGet<{ platforms: Record<string, boolean> }>('/social/config') })
  const rendering = (posts || []).filter((p) => p.status === 'rendering').length

  async function generate() {
    setGenerating(true)
    try {
      const r = await apiPost<{ summary: string }>('/social/generate')
      toast(r.summary || 'Content rendered.', 'success')
      qc.invalidateQueries({ queryKey: ['social-posts'] })
    } catch { toast('Generation failed.', 'error') }
    finally { setGenerating(false) }
  }

  async function poll() {
    setPolling(true)
    try {
      const r = await apiPost<{ summary: string }>('/social/poll')
      toast(r.summary || 'Checked.', 'success')
      qc.invalidateQueries({ queryKey: ['social-posts'] })
    } catch { toast('Check failed.', 'error') }
    finally { setPolling(false) }
  }
  async function publish(p: SocialPost) {
    setPublishing(p.id)
    try {
      const r = await apiPost<{ ok: boolean; results: Record<string, { ok: boolean; reason?: string; error?: string }> }>(`/social/posts/${p.id}/publish`)
      const parts = Object.entries(r.results || {}).map(([k, v]) => `${k}: ${v.ok ? '✓' : (v.reason || v.error || '✗')}`)
      toast(parts.join(' · ') || 'Done.', r.ok ? 'success' : 'error')
      qc.invalidateQueries({ queryKey: ['social-posts'] })
    } catch { toast('Publish failed.', 'error') }
    finally { setPublishing(null) }
  }

  const igReady = conf?.platforms?.instagram; const fbReady = conf?.platforms?.facebook
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[12px] text-muted-foreground">
          Picture ads + AI videos generated from your catalog photos.
          {!igReady && !fbReady && ' Connect Meta keys to auto-post; until then use TikTok-style hand-off.'}
        </p>
        {rendering > 0 && (
          <Button size="sm" variant="outline" onClick={poll} disabled={polling}>
            {polling ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Check renders ({rendering})
          </Button>
        )}
        <Button size="sm" className="ml-auto" onClick={generate} disabled={generating}>
          {generating ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Generate this week's content
        </Button>
      </div>
      {!(posts || []).length ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          No content yet — tap <b>Generate</b> and the engine will design ads from your
          catalog photos and queue them here for approval.
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {(posts || []).map((p) => (
            <Card key={p.id} className="overflow-hidden">
              <div className="grid aspect-[4/5] place-items-center bg-muted">
                {p.status === 'rendering'
                  ? <div className="text-center text-muted-foreground">
                      <Loader2 size={26} className="mx-auto animate-spin text-[#6d28d9]" />
                      <div className="mt-2 text-[10px] font-medium">AI video rendering…</div>
                    </div>
                  : p.kind === 'video'
                    ? <video src={p.media_url} controls muted playsInline className="h-full w-full object-cover" />
                    : <img src={p.media_url} alt={p.item_code} loading="lazy" className="h-full w-full object-cover" />}
              </div>
              <div className="p-2.5">
                <div className="flex items-center gap-1.5">
                  {p.kind === 'video' ? <Play size={12} /> : null}
                  <span className="truncate font-display text-[12px] font-bold">{p.item_code}</span>
                  <span className={cn('ml-auto rounded-full px-2 py-0.5 text-[9px] font-semibold',
                    p.status === 'posted' ? 'bg-emerald-100 text-emerald-700'
                      : p.status === 'failed' ? 'bg-red-100 text-red-700'
                        : p.status === 'rendering' ? 'bg-amber-100 text-amber-700'
                          : 'bg-violet-100 text-violet-700')}>
                    {p.status}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">{p.platforms.join(' · ')}</div>
                {p.status !== 'posted' && p.status !== 'rendering' && (
                  <div className="mt-2 flex gap-1.5">
                    <Button size="sm" className="h-7 flex-1 text-[11px]" disabled={publishing === p.id} onClick={() => publish(p)}>
                      {publishing === p.id ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Publish
                    </Button>
                    <Button size="sm" variant="outline" className="h-7 text-[11px]"
                      onClick={() => navigator.clipboard.writeText(p.caption_en).then(() => toast('Caption copied.', 'success'))}>
                      <ClipboardCopy size={12} />
                    </Button>
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

function Results() {
  const { data: k } = useQuery({ queryKey: ['marketing-kpis'], queryFn: () => apiGet<Kpis>('/outreach/kpis') })
  const pct = k ? Math.min(100, Math.round((k.revenue_bhd / k.target_bhd) * 100)) : 0
  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-display text-sm font-semibold">Month pace — {k?.month ?? '…'}</span>
          <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-bold',
            k?.on_track ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700')}>
            {k ? (k.on_track ? 'ON TRACK' : 'BEHIND') : '…'}
          </span>
        </div>
        <div className="mt-2 flex items-end gap-4">
          <div>
            <div className="font-display text-3xl font-extrabold text-[#1a1430]">BHD {Number(k?.revenue_bhd ?? 0).toLocaleString()}</div>
            <div className="text-[11px] text-muted-foreground">booked so far</div>
          </div>
          <div>
            <div className="font-display text-xl font-bold text-[#6d28d9]">→ BHD {Number(k?.projected_bhd ?? 0).toLocaleString()}</div>
            <div className="text-[11px] text-muted-foreground">projected vs 10,000 target</div>
          </div>
          <div className="ml-auto text-right">
            <div className="font-display text-xl font-bold">{k?.reactivated_30d ?? 0}</div>
            <div className="text-[11px] text-muted-foreground">customers reactivated (30d)</div>
          </div>
        </div>
        <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-gradient-to-r from-[#6d28d9] to-[#a855f7]" style={{ width: `${pct}%` }} />
        </div>
      </Card>
      <Card className="p-4">
        <div className="mb-2 font-display text-sm font-semibold">Weekly funnel — touches → orders</div>
        {!(k?.weekly_kpis || []).length ? (
          <p className="py-4 text-center text-[12px] text-muted-foreground">
            No touches logged yet — send your first messages from the Send Center and results appear here.
          </p>
        ) : (
          <table className="w-full text-[12px]">
            <thead><tr className="text-left text-muted-foreground">
              <th className="py-1 font-medium">Week</th><th className="font-medium">Touches</th>
              <th className="font-medium">Customers ordered</th><th className="text-right font-medium">BHD attributed</th>
            </tr></thead>
            <tbody>
              {(k?.weekly_kpis || []).map((w) => (
                <tr key={w.week} className="border-t">
                  <td className="py-1.5">{w.week}</td><td>{w.touches}</td>
                  <td>{w.converted_targets}</td>
                  <td className="text-right font-semibold text-[#6d28d9]">{Number(w.attributed_bhd).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {k?.summary && <p className="text-[12px] leading-relaxed text-muted-foreground">{k.summary}</p>}
    </div>
  )
}

export default function Marketing() {
  const [tab, setTab] = useState<(typeof TABS)[number]['id']>('send')
  return (
    <div className="space-y-4">
      <PageHeader title="Marketing Studio"
        subtitle="Outreach, campaigns & content — every message waits for your tap" />
      <div className="flex gap-1.5 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={cn('flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs font-medium transition',
              tab === t.id ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'bg-white text-muted-foreground hover:border-[#6d28d9]/40')}>
            <t.icon size={13} /> {t.label}
          </button>
        ))}
      </div>
      {tab === 'send' && <SendCenter />}
      {tab === 'contacts' && <Contacts />}
      {tab === 'campaigns' && <Campaigns />}
      {tab === 'content' && <Content />}
      {tab === 'results' && <Results />}
    </div>
  )
}
