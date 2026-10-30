import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Flame, Loader2, Plus, Shapes, Tags, TriangleAlert } from 'lucide-react'
import { apiPatch, apiPost } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { LoadError } from '@/components/LoadError'
import { Badge } from '@/components/ui/badge'
import {
  categoryNames,
  fetchLists,
  ISSUE_FIELDS,
  ISSUE_KINDS,
  notReady,
  type MiBrand,
  type MiIssueKind,
  type MiIssueType,
  type MiLists,
  type MiSupplier,
} from '@/lib/marketIntel'
import { Empty, Loading, NotAvailableYet, Tabs } from './ui'
import { BTN, BTN_GHOST, BTN_OUTLINE, CARD, INPUT, LABEL, SELECT } from './util'

/**
 * Lists (release r8m, plan F3.7, admin only): the suppliers, the brands (with their aliases and their
 * supplier) and the issue types a rep picks from in Spotted (English and Arabic labels, the kind, a
 * safety default, the extra fields the capture asks for, the order). Adding a brand or an issue type
 * here shows in the capture with no deploy. Nothing is deleted: a row is switched off, or merged into
 * another (history keeps its name). Renaming a brand keeps the old name as an alias (server-side).
 * Categories come from the catalog's own master and are shown here for reference.
 */

type Section = 'suppliers' | 'brands' | 'issue-types' | 'categories'

const KIND_ORDER: MiIssueKind[] = ['quality', 'packaging', 'demand', 'competitor', 'other']

function splitAliases(s: string): string[] {
  return Array.from(new Set(s.split(',').map((x) => x.trim()).filter(Boolean)))
}

/** "Not charging" -> "not_charging" (the issue type's stable code). */
function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
}

function useListWrite() {
  const toast = useToast()
  const qc = useQueryClient()
  return {
    done: (msg: string) => {
      toast(msg, 'success')
      qc.invalidateQueries({ queryKey: ['mi-lists'] })
    },
    fail: (fallback: string) => (e: unknown) => toast(errorText(e, fallback), 'error'),
  }
}

export function ListsTab() {
  const [section, setSection] = useState<Section>('issue-types')
  const q = useQuery({ queryKey: ['mi-lists', 'all'], queryFn: () => fetchLists(true), retry: (n, e) => !notReady(e) && n < 1 })
  if (q.isError && notReady(q.error)) return <NotAvailableYet what="The lists (suppliers, brands, issue types)" />
  if (q.isError) return <LoadError error={q.error} onRetry={() => q.refetch()} isRetrying={q.isFetching} />
  if (!q.data) return <Loading />
  if (!q.data.available) return <NotAvailableYet what="The lists (suppliers, brands, issue types)" />
  const lists = q.data
  return (
    <div>
      <p className="mb-3 max-w-3xl text-[12.5px] text-muted-foreground">
        What reps pick from in Spotted and how the office files reports. A new brand or issue type shows in the capture at once. Rows are never deleted: switch one off, or merge it into another.
      </p>
      <Tabs
        size="sm"
        value={section}
        onChange={setSection}
        tabs={[
          { value: 'issue-types', label: 'Issue types', count: lists.issue_types.length, icon: <TriangleAlert size={14} aria-hidden="true" /> },
          { value: 'brands', label: 'Brands', count: lists.brands.length, icon: <Tags size={14} aria-hidden="true" /> },
          { value: 'suppliers', label: 'Suppliers', count: lists.suppliers.length, icon: <Building2 size={14} aria-hidden="true" /> },
          { value: 'categories', label: 'Categories', icon: <Shapes size={14} aria-hidden="true" /> },
        ]}
      />
      {section === 'suppliers' && <Suppliers lists={lists} />}
      {section === 'brands' && <Brands lists={lists} />}
      {section === 'issue-types' && <IssueTypes lists={lists} />}
      {section === 'categories' && <Categories lists={lists} />}
    </div>
  )
}

function ActiveBadge({ active, merged }: { active: boolean; merged?: string | null }) {
  if (merged) return <Badge tone="grey">Merged into {merged}</Badge>
  return active ? <Badge tone="green">Active</Badge> : <Badge tone="grey">Off</Badge>
}

/* ───────────────────────── suppliers ───────────────────────── */

function Suppliers({ lists }: { lists: MiLists }) {
  const { done, fail } = useListWrite()
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const add = useMutation({
    mutationFn: () => apiPost('/market-intel/lists/suppliers', { name: name.trim(), code: code.trim() || null }),
    onSuccess: () => {
      setName('')
      setCode('')
      done('Supplier added.')
    },
    onError: fail('Could not add the supplier.'),
  })
  const byId = new Map(lists.suppliers.map((s) => [s.id, s.name]))
  return (
    <div className="space-y-4">
      <section className={CARD}>
        <h3 className="mb-2 font-display text-[14px] font-bold">Add a supplier</h3>
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[200px] flex-1">
            <label htmlFor="sup-name" className={LABEL}>Name</label>
            <input id="sup-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} className={INPUT} />
          </div>
          <div className="w-40">
            <label htmlFor="sup-code" className={LABEL}>Code (optional)</label>
            <input id="sup-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={40} className={INPUT} />
          </div>
          <button type="button" disabled={!name.trim() || add.isPending} onClick={() => add.mutate()} className={BTN}>
            {add.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />} Add
          </button>
        </div>
      </section>
      {lists.suppliers.length ? (
        <ul className="space-y-2">
          {[...lists.suppliers].sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name)).map((s) => (
            <SupplierRow key={`${s.id}-${s.name}-${s.active}-${s.merged_into ?? ''}`} s={s} others={lists.suppliers.filter((x) => x.id !== s.id && x.active && !x.merged_into)} mergedName={s.merged_into ? byId.get(s.merged_into) || `#${s.merged_into}` : null} />
          ))}
        </ul>
      ) : (
        <Empty icon={<Building2 size={18} aria-hidden="true" />}>No suppliers yet. Add the first one above.</Empty>
      )}
    </div>
  )
}

function SupplierRow({ s, others, mergedName }: { s: MiSupplier; others: MiSupplier[]; mergedName: string | null }) {
  const { done, fail } = useListWrite()
  const [edit, setEdit] = useState(false)
  const [name, setName] = useState(s.name)
  const [code, setCode] = useState(s.code || '')
  const [active, setActive] = useState(s.active)
  const [into, setInto] = useState('')
  const body: Record<string, unknown> = {}
  if (name.trim() && name.trim() !== s.name) body.name = name.trim()
  if (code.trim() !== (s.code || '')) body.code = code.trim() || null
  if (active !== s.active) body.active = active
  const save = useMutation({
    mutationFn: (b: Record<string, unknown>) => apiPatch(`/market-intel/lists/suppliers/${s.id}`, b),
    onSuccess: () => done('Supplier saved.'),
    onError: fail('Could not save the supplier.'),
  })
  return (
    <li className="rounded-2xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{s.name}</span>
        {s.code && <span className="font-mono text-[12px] text-muted-foreground">{s.code}</span>}
        <ActiveBadge active={s.active} merged={mergedName} />
        <button type="button" onClick={() => setEdit((v) => !v)} className={cn(BTN_GHOST, 'ml-auto')}>{edit ? 'Close' : 'Edit'}</button>
      </div>
      {edit && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[200px] flex-1">
              <label className={LABEL} htmlFor={`sn-${s.id}`}>Name</label>
              <input id={`sn-${s.id}`} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} className={INPUT} />
            </div>
            <div className="w-40">
              <label className={LABEL} htmlFor={`sc-${s.id}`}>Code</label>
              <input id={`sc-${s.id}`} value={code} onChange={(e) => setCode(e.target.value)} maxLength={40} className={INPUT} />
            </div>
            <label className="flex h-10 items-center gap-2 text-[13px] font-semibold">
              <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} className="h-4 w-4 accent-primary" /> Active
            </label>
            <button type="button" disabled={!Object.keys(body).length || save.isPending} onClick={() => save.mutate(body)} className={BTN}>
              {save.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save
            </button>
          </div>
          {!s.merged_into && others.length > 0 && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-[200px] flex-1">
                <label className={LABEL} htmlFor={`sm-${s.id}`}>Merge into (a duplicate of)</label>
                <select id={`sm-${s.id}`} value={into} onChange={(e) => setInto(e.target.value)} className={cn(SELECT, 'w-full')}>
                  <option value="">Pick the supplier to keep</option>
                  {others.map((o) => <option key={o.id} value={String(o.id)}>{o.name}</option>)}
                </select>
              </div>
              <button type="button" disabled={!into || save.isPending} onClick={() => save.mutate({ merged_into: Number(into) })} className={BTN_OUTLINE}>
                Merge
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

/* ───────────────────────── brands ───────────────────────── */

function Brands({ lists }: { lists: MiLists }) {
  const { done, fail } = useListWrite()
  const [name, setName] = useState('')
  const [supplierId, setSupplierId] = useState('')
  const [aliases, setAliases] = useState('')
  const add = useMutation({
    mutationFn: () =>
      apiPost('/market-intel/lists/brands', { name: name.trim(), supplier_id: supplierId ? Number(supplierId) : null, aliases: splitAliases(aliases) }),
    onSuccess: () => {
      setName('')
      setAliases('')
      done('Brand added.')
    },
    onError: fail('Could not add the brand.'),
  })
  const suppliers = lists.suppliers.filter((s) => s.active && !s.merged_into)
  const supplierName = new Map(lists.suppliers.map((s) => [s.id, s.name]))
  const brandName = new Map(lists.brands.map((b) => [b.id, b.name]))
  return (
    <div className="space-y-4">
      <section className={CARD}>
        <h3 className="mb-2 font-display text-[14px] font-bold">Add a brand</h3>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_200px]">
          <div>
            <label htmlFor="br-name" className={LABEL}>Name</label>
            <input id="br-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} className={INPUT} />
          </div>
          <div>
            <label htmlFor="br-sup" className={LABEL}>Supplier</label>
            <select id="br-sup" value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={cn(SELECT, 'w-full')}>
              <option value="">Not known</option>
              {suppliers.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label htmlFor="br-alias" className={LABEL}>Other spellings (comma separated, optional)</label>
            <input id="br-alias" value={aliases} onChange={(e) => setAliases(e.target.value)} maxLength={400} className={INPUT} placeholder="e.g. how reps or packs write it" />
          </div>
        </div>
        <button type="button" disabled={!name.trim() || add.isPending} onClick={() => add.mutate()} className={cn(BTN, 'mt-2')}>
          {add.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />} Add
        </button>
      </section>
      {lists.brands.length ? (
        <ul className="space-y-2">
          {[...lists.brands].sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name)).map((b) => (
            <BrandRow
              key={`${b.id}-${b.name}-${b.active}-${b.supplier_id ?? ''}-${b.aliases.join('|')}-${b.merged_into ?? ''}`}
              b={b}
              suppliers={suppliers}
              supplierName={b.supplier_id ? supplierName.get(b.supplier_id) || `#${b.supplier_id}` : null}
              mergedName={b.merged_into ? brandName.get(b.merged_into) || `#${b.merged_into}` : null}
              others={lists.brands.filter((x) => x.id !== b.id && x.active && !x.merged_into)}
            />
          ))}
        </ul>
      ) : (
        <Empty icon={<Tags size={18} aria-hidden="true" />}>No brands yet. Add the first one above.</Empty>
      )}
    </div>
  )
}

function BrandRow({ b, suppliers, supplierName, mergedName, others }: { b: MiBrand; suppliers: MiSupplier[]; supplierName: string | null; mergedName: string | null; others: MiBrand[] }) {
  const { done, fail } = useListWrite()
  const [edit, setEdit] = useState(false)
  const [name, setName] = useState(b.name)
  const [supplierId, setSupplierId] = useState(b.supplier_id ? String(b.supplier_id) : '')
  const [aliases, setAliases] = useState(b.aliases.join(', '))
  const [active, setActive] = useState(b.active)
  const [into, setInto] = useState('')
  const body: Record<string, unknown> = {}
  if (name.trim() && name.trim() !== b.name) body.name = name.trim()
  if ((supplierId ? Number(supplierId) : null) !== (b.supplier_id ?? null)) body.supplier_id = supplierId ? Number(supplierId) : null
  const nextAliases = splitAliases(aliases)
  if (nextAliases.join('|') !== b.aliases.join('|')) body.aliases = nextAliases
  if (active !== b.active) body.active = active
  const save = useMutation({
    mutationFn: (x: Record<string, unknown>) => apiPatch(`/market-intel/lists/brands/${b.id}`, x),
    onSuccess: () => done('Brand saved.'),
    onError: fail('Could not save the brand.'),
  })
  return (
    <li className="rounded-2xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{b.name}</span>
        <ActiveBadge active={b.active} merged={mergedName} />
        {supplierName && <span className="text-[12px] text-muted-foreground">Supplier: {supplierName}</span>}
        <button type="button" onClick={() => setEdit((v) => !v)} className={cn(BTN_GHOST, 'ml-auto')}>{edit ? 'Close' : 'Edit'}</button>
      </div>
      {(b.aliases.length > 0 || b.renamed_from) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted-foreground">
          {b.aliases.map((a) => (
            <span key={a} className="rounded-full bg-muted px-2 py-0.5">{a}</span>
          ))}
          {b.renamed_from && <span>· renamed from {b.renamed_from}</span>}
        </div>
      )}
      {edit && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_200px]">
            <div>
              <label className={LABEL} htmlFor={`bn-${b.id}`}>Name</label>
              <input id={`bn-${b.id}`} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} className={INPUT} />
              {body.name !== undefined && <p className="mt-1 text-[11.5px] text-muted-foreground">The old name is kept as an alias, so past reports still find it.</p>}
            </div>
            <div>
              <label className={LABEL} htmlFor={`bs-${b.id}`}>Supplier</label>
              <select id={`bs-${b.id}`} value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={cn(SELECT, 'w-full')}>
                <option value="">Not known</option>
                {suppliers.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className={LABEL} htmlFor={`ba-${b.id}`}>Other spellings (comma separated)</label>
              <input id={`ba-${b.id}`} value={aliases} onChange={(e) => setAliases(e.target.value)} maxLength={400} className={INPUT} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex h-10 items-center gap-2 text-[13px] font-semibold">
              <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} className="h-4 w-4 accent-primary" /> Active
            </label>
            <button type="button" disabled={!Object.keys(body).length || save.isPending} onClick={() => save.mutate(body)} className={BTN}>
              {save.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save
            </button>
          </div>
          {!b.merged_into && others.length > 0 && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-[200px] flex-1">
                <label className={LABEL} htmlFor={`bm-${b.id}`}>Merge into (a duplicate of)</label>
                <select id={`bm-${b.id}`} value={into} onChange={(e) => setInto(e.target.value)} className={cn(SELECT, 'w-full')}>
                  <option value="">Pick the brand to keep</option>
                  {others.map((o) => <option key={o.id} value={String(o.id)}>{o.name}</option>)}
                </select>
              </div>
              <button type="button" disabled={!into || save.isPending} onClick={() => save.mutate({ merged_into: Number(into) })} className={BTN_OUTLINE}>
                Merge
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

/* ───────────────────────── issue types ───────────────────────── */

interface IssueDraft {
  kind: MiIssueKind
  code: string
  label_en: string
  label_ar: string
  safety_default: boolean
  fields: string[]
  active: boolean
  sort: string
}

function IssueForm({ draft, onChange, codeLocked }: { draft: IssueDraft; onChange: (d: IssueDraft) => void; codeLocked: boolean }) {
  const set = <K extends keyof IssueDraft>(k: K, v: IssueDraft[K]) => onChange({ ...draft, [k]: v })
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className={LABEL}>
            Label (English)
            <input
              value={draft.label_en}
              onChange={(e) => onChange({ ...draft, label_en: e.target.value, code: codeLocked || draft.code !== slug(draft.label_en) ? draft.code : slug(e.target.value) })}
              maxLength={80}
              className={cn(INPUT, 'mt-1 normal-case tracking-normal')}
              placeholder="e.g. Stops charging"
            />
          </label>
        </div>
        <div>
          <label className={LABEL}>
            Label (Arabic)
            <input dir="rtl" lang="ar" value={draft.label_ar} onChange={(e) => set('label_ar', e.target.value)} maxLength={80} className={cn(INPUT, 'mt-1 normal-case tracking-normal')} />
          </label>
        </div>
        <div>
          <label className={LABEL}>
            Kind
            <select value={draft.kind} onChange={(e) => set('kind', e.target.value as MiIssueKind)} className={cn(SELECT, 'mt-1 w-full normal-case tracking-normal')}>
              {ISSUE_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </label>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_90px] gap-2">
          <label className={LABEL}>
            Code
            <input
              value={draft.code}
              disabled={codeLocked}
              onChange={(e) => set('code', slug(e.target.value))}
              maxLength={40}
              className={cn(INPUT, 'mt-1 font-mono normal-case tracking-normal disabled:opacity-60')}
            />
          </label>
          <label className={LABEL}>
            Order
            <input inputMode="numeric" value={draft.sort} onChange={(e) => set('sort', e.target.value.replace(/\D/g, '').slice(0, 4))} className={cn(INPUT, 'mt-1 tabular-nums normal-case tracking-normal')} />
          </label>
        </div>
      </div>
      <fieldset>
        <legend className={LABEL}>The capture also asks for</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {ISSUE_FIELDS.map((f) => (
            <label key={f.value} className="flex min-h-9 items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={draft.fields.includes(f.value)}
                onChange={(e) => set('fields', e.target.checked ? [...draft.fields, f.value] : draft.fields.filter((x) => x !== f.value))}
                className="h-4 w-4 accent-primary"
              />
              {f.label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        <label className="flex min-h-9 items-center gap-2 text-[13px] font-semibold text-destructive">
          <input type="checkbox" checked={draft.safety_default} onChange={(e) => set('safety_default', e.target.checked)} className="h-4 w-4 accent-destructive" />
          Safety issue (alerts the manager)
        </label>
        <label className="flex min-h-9 items-center gap-2 text-[13px] font-semibold">
          <input type="checkbox" checked={draft.active} onChange={(e) => set('active', e.target.checked)} className="h-4 w-4 accent-primary" />
          Active (reps can pick it)
        </label>
      </div>
    </div>
  )
}

const NEW_ISSUE: IssueDraft = { kind: 'quality', code: '', label_en: '', label_ar: '', safety_default: false, fields: ['qty'], active: true, sort: '100' }

function issuePayload(d: IssueDraft): Record<string, unknown> {
  return {
    kind: d.kind,
    code: d.code,
    label_en: d.label_en.trim(),
    label_ar: d.label_ar.trim() || null,
    safety_default: d.safety_default,
    fields: d.fields,
    active: d.active,
    sort: Number(d.sort || 100),
  }
}

function IssueTypes({ lists }: { lists: MiLists }) {
  const { done, fail } = useListWrite()
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<IssueDraft>(NEW_ISSUE)
  const add = useMutation({
    mutationFn: () => apiPost('/market-intel/lists/issue-types', issuePayload(draft)),
    onSuccess: () => {
      setDraft(NEW_ISSUE)
      setAdding(false)
      done('Issue type added. Reps see it in Spotted now.')
    },
    onError: fail('Could not add the issue type.'),
  })
  const byKind = KIND_ORDER.map((k) => ({
    kind: k,
    rows: lists.issue_types.filter((t) => t.kind === k).sort((a, b) => (a.sort ?? 100) - (b.sort ?? 100) || a.label_en.localeCompare(b.label_en)),
  })).filter((g) => g.rows.length > 0)
  return (
    <div className="space-y-4">
      <section className={CARD}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="font-display text-[14px] font-bold">Issue types</h3>
            <p className="text-[12px] text-muted-foreground">Quality and packaging types are the chips a rep taps in Spotted; a safety type alerts the manager.</p>
          </div>
          {!adding && (
            <button type="button" onClick={() => setAdding(true)} className={BTN}>
              <Plus size={15} aria-hidden="true" /> Add an issue type
            </button>
          )}
        </div>
        {adding && (
          <div className="mt-3 border-t border-border pt-3">
            <IssueForm draft={draft} onChange={setDraft} codeLocked={false} />
            <div className="mt-3 flex gap-2">
              <button type="button" disabled={!draft.label_en.trim() || !draft.code || add.isPending} onClick={() => add.mutate()} className={BTN}>
                {add.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Add
              </button>
              <button type="button" onClick={() => { setAdding(false); setDraft(NEW_ISSUE) }} className={BTN_OUTLINE}>Cancel</button>
            </div>
          </div>
        )}
      </section>
      {byKind.length ? (
        byKind.map((g) => (
          <section key={g.kind}>
            <h3 className="mb-2 font-display text-[14px] font-bold">{ISSUE_KINDS.find((k) => k.value === g.kind)?.label}</h3>
            <ul className="space-y-2">
              {g.rows.map((t) => (
                <IssueRow key={`${t.id}-${t.label_en}-${t.label_ar ?? ''}-${t.active}-${t.sort}-${t.safety_default}-${t.fields.join('|')}-${t.kind}`} t={t} />
              ))}
            </ul>
          </section>
        ))
      ) : (
        <Empty icon={<TriangleAlert size={18} aria-hidden="true" />}>No issue types yet. Add the first ones (e.g. stops charging, cable breaks, box crushed).</Empty>
      )}
    </div>
  )
}

function IssueRow({ t }: { t: MiIssueType }) {
  const { done, fail } = useListWrite()
  const [edit, setEdit] = useState(false)
  const start: IssueDraft = {
    kind: t.kind,
    code: t.code,
    label_en: t.label_en,
    label_ar: t.label_ar || '',
    safety_default: Boolean(t.safety_default),
    fields: [...(t.fields || [])],
    active: t.active,
    sort: String(t.sort ?? 100),
  }
  const [draft, setDraft] = useState<IssueDraft>(start)
  const next = issuePayload(draft)
  const was = issuePayload(start)
  const body: Record<string, unknown> = {}
  for (const k of Object.keys(next)) {
    if (k === 'code') continue // the code is the type's identity in history: fixed once created
    if (JSON.stringify(next[k]) !== JSON.stringify(was[k])) body[k] = next[k]
  }
  const save = useMutation({
    mutationFn: () => apiPatch(`/market-intel/lists/issue-types/${t.id}`, body),
    onSuccess: () => done('Issue type saved.'),
    onError: fail('Could not save the issue type.'),
  })
  return (
    <li className="rounded-2xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        {t.safety_default && <Flame size={15} className="text-destructive" aria-label="Safety" />}
        <span className="font-semibold">{t.label_en}</span>
        {t.label_ar && (
          <span dir="rtl" lang="ar" className="text-[13px] text-muted-foreground">
            {t.label_ar}
          </span>
        )}
        <span className="font-mono text-[11.5px] text-muted-foreground">{t.code}</span>
        <ActiveBadge active={t.active} />
        {/* a chip a rep taps in Spotted: without its Arabic words the Arabic sheet shows the English */}
        {!t.label_ar && (t.kind === 'quality' || t.kind === 'packaging') && (
          <span title="Reps who use Spotted in Arabic see the English label">
            <Badge tone="amber">No Arabic label</Badge>
          </span>
        )}
        <button type="button" onClick={() => setEdit((v) => !v)} className={cn(BTN_GHOST, 'ml-auto')}>{edit ? 'Close' : 'Edit'}</button>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted-foreground">
        <span>Order {t.sort ?? 100}</span>
        {(t.fields || []).map((f) => (
          <span key={f} className="rounded-full bg-muted px-2 py-0.5">{ISSUE_FIELDS.find((x) => x.value === f)?.label || f}</span>
        ))}
        {t.safety_default && <Badge tone="rose">Safety</Badge>}
      </div>
      {edit && (
        <div className="mt-3 border-t border-border pt-3">
          <IssueForm draft={draft} onChange={setDraft} codeLocked />
          <div className="mt-3 flex gap-2">
            <button type="button" disabled={!Object.keys(body).length || !draft.label_en.trim() || save.isPending} onClick={() => save.mutate()} className={BTN}>
              {save.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save
            </button>
            <button type="button" onClick={() => { setDraft(start); setEdit(false) }} className={BTN_OUTLINE}>Cancel</button>
          </div>
        </div>
      )}
    </li>
  )
}

/* ───────────────────────── categories (read-only: the catalog's master) ───────────────────────── */

function Categories({ lists }: { lists: MiLists }) {
  const cats = categoryNames(lists)
  return (
    <section className={CARD}>
      <h3 className="mb-1 font-display text-[14px] font-bold">Categories</h3>
      <p className="mb-3 text-[12px] text-muted-foreground">These come from the catalog's category master, so the capture and the catalog always agree. Change them in the Catalog.</p>
      {cats.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {cats.map((c) => (
            <li key={c} className="rounded-full bg-muted px-3 py-1 text-[12.5px] font-medium">
              {c}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-muted-foreground">No categories came with the lists.</p>
      )}
    </section>
  )
}
