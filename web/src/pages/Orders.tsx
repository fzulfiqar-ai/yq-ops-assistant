import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { UploadCloud, Loader2, FileText, ArrowUpRight, ArrowDownRight, PackageOpen, Sparkles, ClipboardCopy, Flame, Workflow, ChevronRight, AlertTriangle, CheckCircle2, Play, Trash2, FileSpreadsheet, ShieldCheck, XCircle } from 'lucide-react'
import { apiGet, apiUpload, apiPost, apiDelete, apiDownload, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { PageHeader } from '@/components/PageHeader'
import { useToast } from '@/components/Toast'
import { Card } from '@/components/ui/card'

interface ProposalLine {
  item_name: string
  current_stock: number
  avg_daily: number
  days_cover: number | null
  suggested_qty: number
  cover_at_qty_days: number | null
  last_vendor: string | null
  last_rate_bhd: number | null
  vfan_rmb: number | null
  vfan_change_pct: number | null
  cost_bhd: number | null
  sell_bhd: number | null
  margin_pct: number | null
  est_cost_bhd: number | null
  flags: string[]
  urgency: 'urgent' | 'soon'
  reason: string
}
interface VendorGroup { vendor: string; lines: number; est_total_bhd: number; draft_message: string; items: ProposalLine[] }
interface Proposal { count: number; urgent_count: number; vendor_count: number; est_total_bhd: number; summary: string; lines: ProposalLine[]; by_vendor: VendorGroup[] }

interface POData {
  proposal?: Proposal
  recent: { po_no: string; po_date: string; vendor: string; lines: number; value_bhd: number; received?: boolean }[]
  cost_changes: { item_code: string; prev_rate_bhd: number; current_rate_bhd: number; rate_change_pct: number; prev_ordered: string; last_ordered: string }[]
  on_order: { po_no: string; code: string; qty_ordered: number; rate_bhd: number; po_date: string }[]
}

interface BoardOrder {
  id: number; ref: string | null; title: string; vendor: string | null; stage: string
  est_value_bhd: number | null; po_no: string | null; note: string | null
  days_in_stage: number; sla_days: number | null; is_stuck: boolean
}
interface BoardData {
  stages: { key: string; label: string }[]
  orders: BoardOrder[]; open_count: number; stuck_count: number; pipeline_value_bhd: number; summary: string
}
interface SupplierPriceRow { model: string; latest_rmb: number; prev_rmb: number | null; change_pct: number | null; latest_date: string; latest_invoice: string; invoice_count: number }
interface SupplierPrices { count: number; changed_count: number; rows: SupplierPriceRow[] }

type CheckStatus = 'ok' | 'warn' | 'fail' | 'info'
interface VerifyCheck { name: string; status: CheckStatus; note: string }
interface VerifyLine {
  model: string; spec: string | null; qty: number
  unit_price_rmb: number | null; net_price_rmb: number | null; amount_rmb: number | null
  cost_bhd: number | null; sell_bhd: number | null; margin_pct: number | null
  cover_days: number | null; status: 'ok' | 'warn' | 'fail'; checks: VerifyCheck[]
}
interface VerifyReport {
  ok: boolean; verdict: 'pass' | 'review' | 'fail' | 'unreadable'; flags: number
  fails?: number; warns?: number; lines: VerifyLine[]; total_amount_rmb?: number
  summary: string; vendor?: string
}

const bhd = (n?: number) => (n == null ? '—' : `BHD ${Number(n).toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`)

export default function Orders() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['purchase-orders'], queryFn: () => apiGet<POData>('/purchase-orders') })
  const { data: board } = useQuery({ queryKey: ['procurement-board'], queryFn: () => apiGet<BoardData>('/procurement/board') })
  const { data: supplierPrices } = useQuery({ queryKey: ['supplier-prices'], queryFn: () => apiGet<SupplierPrices>('/supplier-prices') })
  const { me } = useAuth()
  const isAdmin = me?.role === 'admin'

  async function delOrder(poNo: string) {
    if (!window.confirm(`Delete order ${poNo} and all its files? This can't be undone.`)) return
    try {
      const r = await apiDelete<{ ok?: boolean; error?: string }>(`/orders/${encodeURIComponent(poNo)}`)
      if (r.error) toast(r.error, 'error')
      else { toast(`Order ${poNo} deleted.`, 'success'); qc.invalidateQueries({ queryKey: ['purchase-orders'] }) }
    } catch { toast('Could not delete the order.', 'error') }
  }
  const inputRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  // Per-item quantity overrides so you can tune the AI proposal before raising the order.
  const [qtyEdits, setQtyEdits] = useState<Record<string, number>>({})
  const qtyFor = (i: ProposalLine) => qtyEdits[i.item_name] ?? i.suggested_qty
  const unitCost = (i: ProposalLine) => i.cost_bhd ?? i.last_rate_bhd ?? null
  const estFor = (i: ProposalLine) => { const c = unitCost(i); return c != null ? qtyFor(i) * c : i.est_cost_bhd ?? null }
  const toast = useToast()

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setBusy(true)
    let pos = 0, mrns = 0, invs = 0
    for (const f of Array.from(files)) {
      const low = f.name.toLowerCase()
      const isPacking = /\bpl\b/.test(low) || low.includes('packing')
      const isInvoice = !isPacking && (low.endsWith('.xls') || low.endsWith('.xlsx') || low.includes('invoice')
        || low.includes('proforma') || /vf\d{6}/.test(low) || /^20\d{2}[.\-_ ]?\d{2}/.test(f.name))
      try {
        const fd = new FormData(); fd.append('file', f)
        if (low.endsWith('.xml')) {
          const r = await apiUpload<{ skus?: number; docs?: string[]; error?: string }>('/material-receipts/upload', fd)
          if (r.error) toast(`${f.name}: ${r.error}`, 'error')
          else { mrns++; toast(`Receipt ${(r.docs || []).join(', ')} — ${r.skus} item costs updated.`, 'success') }
        } else if (isPacking) {
          const r = await apiUpload<{ ok?: boolean; po_no?: string; error?: string }>('/orders/attach-doc', fd)
          if (r.error) toast(`${f.name}: ${r.error}`, 'error')
          else { pos++; toast(`${f.name} attached to ${r.po_no}.`, 'success') }
        } else if (isInvoice) {
          const r = await apiUpload<{ models?: number; invoice?: string; error?: string }>('/invoices/upload', fd)
          if (r.error) toast(`${f.name}: ${r.error}`, 'error')
          else { invs++; toast(`Invoice ${r.invoice} — ${r.models} supplier prices tracked.`, 'success') }
        } else if (low.endsWith('.pdf')) {
          const r = await apiUpload<{ po_no?: string; lines?: number; error?: string }>('/purchase-orders/upload', fd)
          if (r.error) toast(`${f.name}: ${r.error}`, 'error')
          else { pos++; toast(`${r.po_no} loaded (${r.lines} lines).`, 'success') }
        } else {
          toast(`${f.name}: open the order and use “Attach file” for packing lists or photos.`, 'error')
        }
      } catch (e) {
        toast(e instanceof ApiError ? `${f.name}: ${e.status}` : `${f.name}: upload failed`, 'error')
      }
    }
    setBusy(false)
    if (pos) qc.invalidateQueries({ queryKey: ['purchase-orders'] })
    if (mrns) qc.invalidateQueries({ queryKey: ['report', 'dashboard'] })  // costs → margins changed
    if (invs) qc.invalidateQueries({ queryKey: ['supplier-prices'] })
  }

  async function copyDraft(text: string) {
    try { await navigator.clipboard.writeText(text); toast('Draft order copied — paste it to the vendor.', 'success') }
    catch { toast('Could not copy.', 'error') }
  }

  async function startOrder(g: VendorGroup) {
    try {
      const lines = g.items.map((i) => ({ item: i.item_name, qty: qtyFor(i), rate: i.last_rate_bhd }))
      const estTotal = g.items.reduce((s, i) => s + (estFor(i) ?? 0), 0)
      const namedVendor = g.vendor.startsWith('(') ? null : g.vendor
      await apiPost('/procurement/orders', {
        title: `${namedVendor || 'New'} reorder`, vendor: namedVendor, est_value_bhd: estTotal, lines,
      })
      toast(`Order started${namedVendor ? ` for ${namedVendor}` : ''} — track it in the pipeline below.`, 'success')
      qc.invalidateQueries({ queryKey: ['procurement-board'] })
    } catch { toast('Could not start the order.', 'error') }
  }

  // Export the reviewed proposal as a VFAN-format order .xlsx. The proposal carries the NET ¥ price
  // (after the 18% discount); the sheet shows list → DIS → net, so we reconstruct the list price.
  async function exportOrder(g: VendorGroup) {
    try {
      const lines = g.items.map((i) => {
        const parts = i.item_name.split(' ')
        const list = i.vfan_rmb != null ? Math.round((i.vfan_rmb / 0.82) * 100) / 100 : null
        return { model: parts[0], spec: parts.slice(1).join(' '), qty: qtyFor(i), unit_price_rmb: list }
      })
      const vendor = g.vendor.startsWith('(') ? 'VFAN' : g.vendor
      await apiDownload('/orders/proposal/export', { vendor, lines }, `YQ Order ${vendor}.xlsx`)
      toast('Order Excel downloaded — review it, then send to the vendor.', 'success')
    } catch { toast('Could not export the order.', 'error') }
  }

  // ── Verify a vendor order .xlsx (the human-in-the-loop gate) ──
  const verifyRef = useRef<HTMLInputElement>(null)
  const [vdrag, setVdrag] = useState(false)
  const [verifyBusy, setVerifyBusy] = useState(false)
  const [verify, setVerify] = useState<VerifyReport | null>(null)

  async function runVerify(files: FileList | null) {
    const f = files?.[0]
    if (!f) return
    if (!/\.(xls|xlsx)$/i.test(f.name)) { toast('Upload the order as Excel (.xlsx).', 'error'); return }
    setVerifyBusy(true)
    try {
      const fd = new FormData(); fd.append('file', f)
      const r = await apiUpload<VerifyReport>('/orders/verify', fd)
      setVerify(r)
      if (!r.ok || r.verdict === 'unreadable') toast(r.summary || 'Could not read the order.', 'error')
    } catch { toast('Verify failed.', 'error') } finally { setVerifyBusy(false) }
  }

  async function approveVerified() {
    if (!verify?.lines?.length) return
    const lines = verify.lines.map((l) => ({ item: l.model, spec: l.spec, qty: l.qty, rate: l.cost_bhd }))
    const est = verify.lines.reduce((s, l) => s + (l.qty || 0) * (l.cost_bhd || 0), 0)
    try {
      await apiPost('/procurement/orders', {
        title: `${verify.vendor || 'VFAN'} order (verified)`, vendor: verify.vendor || 'VFAN',
        est_value_bhd: Math.round(est * 1000) / 1000, lines, stage: 'reviewed',
        note: `Verified order — ${verify.summary}`,
      })
      toast('Verified order added to the pipeline at “Reviewed”.', 'success')
      setVerify(null)
      qc.invalidateQueries({ queryKey: ['procurement-board'] })
    } catch { toast('Could not add to the pipeline.', 'error') }
  }

  async function advanceOrder(id: number, stage: string, label: string) {
    try {
      await apiPost(`/procurement/orders/${id}/advance`, { stage })
      toast(`Moved to ${label}.`, 'success')
      qc.invalidateQueries({ queryKey: ['procurement-board'] })
    } catch { toast('Could not update the order.', 'error') }
  }

  const proposal = data?.proposal

  return (
    <div>
      <PageHeader title="Orders" subtitle="From AI proposal to delivery — propose, raise, track, and compare every order" />

      <Card className="p-5">
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files) }}
          onClick={() => inputRef.current?.click()}
          className={cn('flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-8 text-center transition',
            drag ? 'border-primary bg-accent' : 'border-border hover:border-primary/50')}>
          <input ref={inputRef} type="file" accept=".pdf,.xml,.xls,.xlsx" multiple className="hidden" onChange={(e) => upload(e.target.files)} />
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-accent text-accent-foreground">
            {busy ? <Loader2 className="animate-spin" size={24} /> : <UploadCloud size={24} />}
          </div>
          <div className="mt-3 font-display text-base font-semibold">Drop a PO (PDF), MRN (XML) or Invoice (PDF / Excel)</div>
          <div className="mt-1 text-sm text-muted-foreground">PO = the order · MRN = real landed cost · Invoice = supplier price history — one or many</div>
        </div>
      </Card>

      {/* AI reorder proposal — the agentic headline (Phase 2) */}
      {proposal && proposal.count > 0 && (
        <div className="mt-6">
          <div className="mb-2 flex items-center gap-2 px-1">
            <Sparkles size={14} className="text-primary" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Suggested order · AI reorder proposal</span>
          </div>
          <Card className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 p-4">
            <div className="text-sm">{proposal.summary}</div>
            {proposal.urgent_count > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-600">
                <Flame size={11} /> {proposal.urgent_count} urgent
              </span>
            )}
          </Card>
          <div className="space-y-3">
            {proposal.by_vendor.map((g) => (
              <Card key={g.vendor} className="overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-accent/30 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="truncate font-display text-sm font-semibold">{g.vendor}</div>
                    <div className="text-[11px] text-muted-foreground">{g.lines} item{g.lines > 1 ? 's' : ''} · est. {bhd(g.est_total_bhd)}</div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button onClick={() => copyDraft(g.draft_message)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:border-primary/50 hover:bg-accent">
                      <ClipboardCopy size={13} /> Copy
                    </button>
                    <button onClick={() => exportOrder(g)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:border-primary/50 hover:bg-accent">
                      <FileSpreadsheet size={13} /> Export Excel
                    </button>
                    <button onClick={() => startOrder(g)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90">
                      <Play size={13} /> Start order
                    </button>
                  </div>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                      <th className="px-4 py-2">Item</th>
                      <th className="px-4 py-2 text-right">Qty</th>
                      <th className="px-4 py-2 text-right">VFAN ¥</th>
                      <th className="px-4 py-2 text-right">Est. cost</th>
                      <th className="px-4 py-2 text-right">Margin</th>
                      <th className="px-4 py-2">Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.items.map((i) => (
                      <tr key={i.item_name} className="border-b last:border-0 hover:bg-accent/30">
                        <td className="px-4 py-2">
                          <span className="font-medium">{i.item_name}</span>
                          {i.urgency === 'urgent' && <Flame size={11} className="ml-1 inline text-rose-500" />}
                        </td>
                        <td className="px-4 py-2 text-right">
                          <input type="number" min={0} value={qtyFor(i)}
                            onChange={(e) => setQtyEdits((s) => ({ ...s, [i.item_name]: Math.max(0, Math.floor(Number(e.target.value) || 0)) }))}
                            className="w-16 rounded-md border border-border bg-background px-2 py-1 text-right text-sm font-semibold tabular-nums outline-none transition focus:border-primary/50" />
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          {i.vfan_rmb == null ? <span className="text-muted-foreground">—</span> : <>¥{i.vfan_rmb}
                            {i.vfan_change_pct != null && Math.abs(i.vfan_change_pct) >= 5 && (
                              <span className={cn('ml-1 text-[11px] font-semibold', i.vfan_change_pct > 0 ? 'text-rose-600' : 'text-emerald-600')}>
                                {i.vfan_change_pct > 0 ? '▲' : '▼'}{Math.abs(i.vfan_change_pct)}%
                              </span>)}</>}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">{estFor(i) == null ? '—' : bhd(estFor(i) as number)}</td>
                        <td className={cn('px-4 py-2 text-right font-semibold tabular-nums', i.margin_pct == null ? 'text-muted-foreground' : i.margin_pct >= 30 ? 'text-emerald-600' : i.margin_pct >= 20 ? 'text-amber-600' : 'text-rose-600')}>
                          {i.margin_pct == null ? '—' : `${i.margin_pct}%`}
                        </td>
                        <td className="px-4 py-2 text-[12px] text-muted-foreground">{i.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            ))}
          </div>
          <div className="mt-2 px-1 text-[11px] text-muted-foreground">
            Drafted from sales velocity + your PO history. Review, adjust, then raise with the vendor — the agent advises, you decide.
          </div>
        </div>
      )}

      {/* Verify a vendor order (Phase 2) — the human gate before paying */}
      <div className="mt-6">
        <div className="mb-2 flex items-center gap-2 px-1">
          <ShieldCheck size={14} className="text-primary" />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Verify a vendor order · price · math · margin · qty</span>
        </div>
        <Card className="p-4">
          <div onDragOver={(e) => { e.preventDefault(); setVdrag(true) }} onDragLeave={() => setVdrag(false)}
            onDrop={(e) => { e.preventDefault(); setVdrag(false); runVerify(e.dataTransfer.files) }}
            onClick={() => verifyRef.current?.click()}
            className={cn('flex cursor-pointer items-center justify-center gap-3 rounded-xl border-2 border-dashed p-5 text-center transition',
              vdrag ? 'border-primary bg-accent' : 'border-border hover:border-primary/50')}>
            <input ref={verifyRef} type="file" accept=".xls,.xlsx" className="hidden" onChange={(e) => runVerify(e.target.files)} />
            {verifyBusy ? <Loader2 className="animate-spin" size={18} /> : <ShieldCheck size={18} className="text-muted-foreground" />}
            <div className="text-sm">
              <span className="font-medium">Drop the order Excel from VFAN to verify</span>
              <span className="ml-1 text-muted-foreground">— price vs last VFAN, the 18% math, margin &amp; qty, before you pay.</span>
            </div>
          </div>

          {verify && verify.ok && verify.lines.length > 0 && (
            <div className="mt-4">
              <div className={cn('flex flex-wrap items-center justify-between gap-3 rounded-xl border p-3',
                verify.verdict === 'pass' ? 'border-emerald-200 bg-emerald-50' : verify.verdict === 'review' ? 'border-amber-200 bg-amber-50' : 'border-rose-200 bg-rose-50')}>
                <div className="flex items-center gap-2 text-sm font-medium">
                  {verify.verdict === 'pass' ? <CheckCircle2 size={16} className="text-emerald-600" />
                    : verify.verdict === 'fail' ? <XCircle size={16} className="text-rose-600" />
                      : <AlertTriangle size={16} className="text-amber-600" />}
                  {verify.summary}
                </div>
                {verify.verdict !== 'fail' && (
                  <button onClick={approveVerified}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90">
                    <Play size={13} /> Approve → pipeline
                  </button>
                )}
              </div>
              <div className="mt-3 overflow-hidden rounded-xl border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-accent/30 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                      <th className="px-3 py-2">Model</th>
                      <th className="px-3 py-2 text-right">Qty</th>
                      <th className="px-3 py-2 text-right">Net ¥</th>
                      <th className="px-3 py-2 text-right">Margin</th>
                      <th className="px-3 py-2">Checks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {verify.lines.map((l, idx) => (
                      <tr key={`${l.model}-${idx}`} className="border-b align-top last:border-0">
                        <td className="px-3 py-2 font-medium">
                          <span className={cn('mr-1.5 inline-block h-2 w-2 rounded-full', l.status === 'ok' ? 'bg-emerald-500' : l.status === 'warn' ? 'bg-amber-500' : 'bg-rose-500')} />
                          {l.model}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{l.qty}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{l.net_price_rmb == null ? '—' : `¥${l.net_price_rmb}`}</td>
                        <td className={cn('px-3 py-2 text-right font-semibold tabular-nums', l.margin_pct == null ? 'text-muted-foreground' : l.margin_pct >= 30 ? 'text-emerald-600' : l.margin_pct >= 20 ? 'text-amber-600' : 'text-rose-600')}>{l.margin_pct == null ? '—' : `${l.margin_pct}%`}</td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap gap-1">
                            {l.checks.filter((c) => c.status === 'warn' || c.status === 'fail').map((c, ci) => (
                              <span key={ci} className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', c.status === 'fail' ? 'bg-rose-100 text-rose-700' : 'bg-amber-100 text-amber-700')}>{c.note}</span>
                            ))}
                            {l.status === 'ok' && <span className="text-[11px] text-emerald-600">all good</span>}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 px-1 text-[11px] text-muted-foreground">
                The agent checks; you approve. Approving adds it to the pipeline at “Reviewed”.
                {verify.total_amount_rmb ? ` Order total ≈ ¥${verify.total_amount_rmb.toLocaleString()}.` : ''}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* Procurement pipeline (Phase 3) — track each order to delivery */}
      {board && board.orders.length > 0 && (
        <div className="mt-6">
          <div className="mb-2 flex flex-wrap items-center gap-2 px-1">
            <Workflow size={14} className="text-primary" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Order pipeline</span>
            {board.stuck_count > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-600">
                <AlertTriangle size={11} /> {board.stuck_count} need attention
              </span>
            )}
          </div>
          <div className="space-y-2.5">
            {board.orders.map((o) => {
              const linear = board.stages.filter((s) => s.key !== 'cancelled')
              const idx = linear.findIndex((s) => s.key === o.stage)
              const next = idx >= 0 && idx < linear.length - 1 ? linear[idx + 1] : null
              const terminal = o.stage === 'closed' || o.stage === 'cancelled'
              return (
                <Card key={o.id} className={cn('p-4', o.is_stuck && 'border-amber-300')}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-display text-sm font-semibold">{o.title}</span>
                        {o.ref && <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground">{o.ref}</span>}
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {o.vendor || 'vendor TBC'}{o.est_value_bhd != null && ` · est. ${bhd(o.est_value_bhd)}`}{o.po_no && ` · Focus ${o.po_no}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {o.is_stuck && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-600">
                          <AlertTriangle size={11} /> {o.days_in_stage}d
                        </span>
                      )}
                      {!terminal && next && (
                        <button onClick={() => advanceOrder(o.id, next.key, next.label)}
                          className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90">
                          {next.label} <ChevronRight size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                  {/* stage track */}
                  <div className="mt-3 flex items-center gap-1">
                    {linear.map((s, si) => (
                      <div key={s.key} title={s.label} className={cn('h-1.5 flex-1 rounded-full', si <= idx ? 'bg-primary' : 'bg-border')} />
                    ))}
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      {(o.stage === 'received' || o.stage === 'closed') && <CheckCircle2 size={12} className="text-emerald-500" />}
                      {linear[idx]?.label || (o.stage === 'cancelled' ? 'Cancelled' : o.stage)}
                      {!terminal && ` · ${o.days_in_stage}d in stage`}
                    </span>
                    {!terminal && (
                      <button onClick={() => advanceOrder(o.id, 'cancelled', 'Cancelled')}
                        className="text-muted-foreground/70 transition hover:text-rose-600">Cancel</button>
                    )}
                  </div>
                </Card>
              )
            })}
          </div>
        </div>
      )}

      {/* cost change across orders — the headline */}
      <div className="mt-6">
        <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Cost change vs the previous order</div>
        {data?.cost_changes?.length ? (
          <Card className="overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                  <th className="px-4 py-2.5">Item</th>
                  <th className="px-4 py-2.5 text-right">Previous</th>
                  <th className="px-4 py-2.5 text-right">Latest</th>
                  <th className="px-4 py-2.5 text-right">Change</th>
                </tr>
              </thead>
              <tbody>
                {data.cost_changes.map((c) => {
                  const up = c.rate_change_pct > 0
                  return (
                    <tr key={c.item_code} className="border-b last:border-0 hover:bg-accent/30">
                      <td className="px-4 py-2.5 font-medium">{c.item_code}</td>
                      <td className="px-4 py-2.5 text-right text-muted-foreground">{bhd(c.prev_rate_bhd)}</td>
                      <td className="px-4 py-2.5 text-right">{bhd(c.current_rate_bhd)}</td>
                      <td className={cn('flex items-center justify-end gap-1 px-4 py-2.5 text-right font-semibold', up ? 'text-rose-600' : 'text-emerald-600')}>
                        {up ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{Math.abs(c.rate_change_pct)}%
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        ) : (
          <Card className="p-6 text-center text-sm text-muted-foreground">
            Upload 2+ orders containing the same item, and the system compares each item's cost for you.
          </Card>
        )}
      </div>

      {/* Supplier price changes — RMB unit prices from the VFAN proforma invoices */}
      {supplierPrices && supplierPrices.rows.length > 0 && (
        <div className="mt-6">
          <div className="mb-2 flex items-center gap-2 px-1">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Supplier price changes · VFAN proforma (¥ RMB)</span>
            {supplierPrices.changed_count > 0 && (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-600">{supplierPrices.changed_count} changed</span>
            )}
          </div>
          <Card className="overflow-hidden">
            <div className="max-h-[360px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card">
                  <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    <th className="px-4 py-2.5">Model</th>
                    <th className="px-4 py-2.5 text-right">Previous ¥</th>
                    <th className="px-4 py-2.5 text-right">Latest ¥</th>
                    <th className="px-4 py-2.5 text-right">Change</th>
                    <th className="px-4 py-2.5 text-right">Invoices</th>
                  </tr>
                </thead>
                <tbody>
                  {supplierPrices.rows.map((r) => {
                    const up = (r.change_pct ?? 0) > 0
                    return (
                      <tr key={r.model} className="border-b last:border-0 hover:bg-accent/30">
                        <td className="px-4 py-2.5 font-medium">{r.model}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground tabular-nums">{r.prev_rmb == null ? '—' : `¥${r.prev_rmb}`}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums">¥{r.latest_rmb}</td>
                        <td className={cn('px-4 py-2.5 text-right font-semibold tabular-nums', r.change_pct == null ? 'text-muted-foreground' : up ? 'text-rose-600' : 'text-emerald-600')}>
                          {r.change_pct == null ? 'new' : `${up ? '▲' : '▼'} ${Math.abs(r.change_pct)}%`}
                        </td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground tabular-nums">{r.invoice_count}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>
          <div className="mt-2 px-1 text-[11px] text-muted-foreground">From your VFAN proforma invoices (after 18% discount). Drop a new invoice above to update.</div>
        </div>
      )}

      {(() => {
        const all = data?.recent || []
        const received = all.filter((o) => o.received)
        const pending = all.filter((o) => !o.received)
        const orderCard = (o: POData['recent'][number]) => (
          <div key={o.po_no} className="group relative">
            <Link to={`/orders/${encodeURIComponent(o.po_no)}`}
              className="flex items-center gap-3 rounded-xl border bg-card p-3.5 text-card-foreground shadow-soft transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lift">
              <div className="grid h-9 w-9 place-items-center rounded-lg bg-accent text-accent-foreground"><FileText size={16} /></div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{o.po_no}</span>
                  <span className={cn('inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
                    o.received ? 'bg-emerald-50 text-emerald-700' : 'bg-sky-50 text-sky-700')}>
                    {o.received ? <CheckCircle2 size={10} /> : <PackageOpen size={10} />}{o.received ? 'Received' : 'On the way'}
                  </span>
                </div>
                <div className="text-[11px] text-muted-foreground">{o.po_date} · {o.vendor} · {o.lines} lines</div>
              </div>
              <div className="text-sm font-semibold">{bhd(o.value_bhd)}</div>
              <ChevronRight size={16} className="text-muted-foreground opacity-0 transition group-hover:opacity-100" />
            </Link>
            {isAdmin && (
              <button onClick={() => delOrder(o.po_no)} title="Delete order (admin)"
                className="absolute -right-2 -top-2 hidden h-6 w-6 place-items-center rounded-full border bg-card text-muted-foreground shadow-soft transition hover:text-rose-600 group-hover:grid">
                <Trash2 size={12} />
              </button>
            )}
          </div>
        )
        return (
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <div>
              <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Received orders ({received.length})</div>
              <div className="space-y-2">
                {received.map(orderCard)}
                {!received.length && <Card className="p-6 text-center text-sm text-muted-foreground">No received orders yet.</Card>}
              </div>
            </div>
            <div>
              <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">On the way · pending ({pending.length})</div>
              <div className="space-y-2">
                {pending.map(orderCard)}
                {!pending.length && <Card className="p-6 text-center text-sm text-muted-foreground">Everything ordered has been received. ✓</Card>}
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
