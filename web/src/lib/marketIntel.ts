import { useSyncExternalStore } from 'react'
import { ApiError, apiGet, apiUpload } from './api'
import { detailText, isNetworkError } from './errorText'

/**
 * Market Intelligence (release R7b, app/market_intel.py): the shapes the API answers, the photo
 * resize the capture sheet runs on the phone, and the in-page retry queue.
 *
 * The queue keeps a capture that could not reach the server (weak signal in a shop, the API
 * waking up) in memory with its client UUID and retries it while the app is open. The server
 * treats the UUID as the capture's identity, so a retry that lands twice is still one sighting.
 * True offline (a service worker) is later (plan §18.1): closing the tab loses what is queued,
 * and the sheet says so.
 *
 * Market Intel v2 (release r8m, plan Part F) adds, further down: the admin lists (suppliers, brands,
 * issue types), the product picker's rows, the quality cases with their eight statuses and the
 * transitions the API allows, the evidence levels, the supplier actions and the monthly supplier
 * reports. The capture keeps its queue: the new fields travel in the same `fields` map.
 */

export type MiKind = 'new_product' | 'competitor_price' | 'promotion' | 'shop_asked' | 'complaint' | 'other'
export type MiResult = 'already_in_yq' | 'seen_before' | 'new_find' | 'saved_for_review'
export type MiStatus = 'new' | 'researching' | 'opportunity' | 'approved' | 'rejected' | 'merged'
export type MiAction = 'source' | 'price_response' | 'promo' | 'ignore'

export const KINDS: { value: MiKind; label: string }[] = [
  { value: 'new_product', label: 'New product' },
  { value: 'competitor_price', label: 'Competitor price' },
  { value: 'promotion', label: 'Promotion' },
  { value: 'shop_asked', label: 'Shop asked for' },
  { value: 'complaint', label: 'Complaint' },
  { value: 'other', label: 'Other' },
]
export const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.value, k.label]))

export const STATUS_LABEL: Record<MiStatus, string> = {
  new: 'New',
  researching: 'Researching',
  opportunity: 'Opportunity',
  approved: 'Approved',
  rejected: 'Rejected',
  merged: 'Merged',
}
export const ACTIONS: { value: MiAction; label: string }[] = [
  { value: 'source', label: 'Source it' },
  { value: 'price_response', label: 'Price response' },
  { value: 'promo', label: 'Run a promotion' },
  { value: 'ignore', label: 'Ignore' },
]
export const DEMAND_LEVELS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
] as const

export interface MiCaps {
  office: boolean
  can_review: boolean
  can_approve: boolean
  can_capture: boolean
  /** v2: an admin (the Lists, a closed case reopened, the unblurred photo): the quality routes send can_admin */
  can_admin?: boolean
  is_admin?: boolean
}

export interface MiYqItem {
  item_code: string
  display_name: string
  category?: string | null
  brand?: string | null
  thumb_url?: string | null
}

export interface MiCard {
  ok: boolean
  result: MiResult
  message: string
  observation_id: number | null
  client_uuid: string
  item: { id: number; title?: string | null; kind: MiKind; kind_label: string; status: MiStatus; status_label: string; observations: number; shops: number; reps: number } | null
  yq_item: MiYqItem | null
  near_duplicate: { observation_id: number } | null
  photos: number
  replayed: boolean
  /** 'legacy' = saved to the old Finds / Field Notes tables (the R7b migration is not applied yet) */
  fallback?: string
  /** v2: a safety report e-mailed the manager (once per case) */
  safety_alerted?: boolean
  /**
   * v2: the quality case a quality / packaging report opened, or joined (`joined`: another report of the
   * same product and issue was open). Built from the rep's own sighting, never another rep's words.
   */
  case?: {
    id: number
    title?: string | null
    status?: MiCaseStatus | null
    status_words?: string | null
    issue?: string | null
    joined?: boolean | null
    sightings?: number | null
    shops?: number | null
    safety?: boolean | null
  } | null
  case_id?: number | null
}

export interface MiCluster {
  item_id: number
  kind: MiKind
  kind_label: string
  title?: string | null
  brand?: string | null
  competitor?: string | null
  category?: string | null
  barcode?: string | null
  yq_item_code?: string | null
  status: MiStatus
  status_label: string
  action?: MiAction | null
  action_label?: string | null
  observations: number
  shops: number
  reps: number
  price_min?: string | null
  price_max?: string | null
  first_seen?: string | null
  last_seen?: string | null
  photos: number
  cover_url?: string | null
  /** v2: the cover's 320 px thumbnail (the board reads it first; the master is the fallback) */
  thumb_url?: string | null
  cover_thumb_url?: string | null
  system_signals: number
  demand_qty?: number | null
  summary: string
  mine?: boolean
  near_duplicates?: number
  merged_into?: number | null
  has_ai_suggestion?: boolean
  ai_confidence?: number | null
  verified?: boolean
  photos_people?: number
}

export interface MiFacets {
  kinds?: { value: MiKind; label: string }[]
  statuses?: { value: MiStatus; label: string; count: number }[]
  actions?: { value: MiAction; label: string }[]
  categories?: string[]
  brands?: string[]
  demand_levels?: string[]
  reps?: { id: number; name: string }[]
  areas?: string[]
}

export interface MiBoard {
  available: boolean
  hint?: string
  items: MiCluster[]
  /** v2: the true total behind ?offset=&limit= paging (v1: the rows returned) */
  count?: number
  offset?: number
  limit?: number
  facets: MiFacets
  queue: { new_items: number; unassigned: number; library: number } | null
  capabilities: MiCaps
}

export interface MiPhoto {
  id: number
  /** a flagged photo (people in it) is signed as the server's blurred copy; admins may ask ?raw=1 */
  url?: string | null
  /** v2: the 320 px WebP for lists and strips */
  thumb_url?: string | null
  width?: number | null
  height?: number | null
  has_people: boolean
  /** v2 payloads name it after the column */
  has_people_flag?: boolean
  /** v2: the urls are the server's blurred copy (a people photo, no admin ?raw=1) */
  blurred?: boolean
  role?: MiPhotoRole | null
  caption?: string | null
}

export interface MiObservation {
  id: number
  kind: MiKind
  kind_label: string
  source: 'rep' | 'import' | 'system'
  signal?: string | null
  title?: string | null
  note?: string | null
  brand?: string | null
  competitor?: string | null
  category?: string | null
  price_bhd?: string | null
  demand_level?: string | null
  demand_qty?: number | null
  barcode?: string | null
  yq_item_code?: string | null
  result?: MiResult | null
  observed_at?: string | null
  area?: string | null
  shop_name?: string | null
  item_id?: number | null
  mine: boolean
  near_duplicate_of?: number | null
  photos: MiPhoto[]
  by?: string
  item_title?: string | null
  item_status?: MiStatus | null
  status_label?: string | null
  item_shops?: number
  /* ── v2 (release r8m): the quality report's own fields ── */
  case_id?: number | null
  issue_type_id?: number | null
  issue_type?: { id: number; code?: string | null; label_en?: string | null; label_ar?: string | null } | null
  issue_label?: string | null
  report_basis?: MiReportBasis | null
  qty_affected?: number | null
  batch_ref?: string | null
  packaging_version?: string | null
  safety_flag?: boolean | null
  client_observed_at?: string | null
  /** "My reports": what became of the case, in plain words ("Being looked at", "We need more from you"…) */
  case_status_words?: string | null
  /** "My reports": the office's open question to the rep, when its case asks one */
  open_question?: string | null
}

export interface MiDecision {
  id: number
  event: 'status' | 'edit' | 'identify' | 'merge' | 'approve'
  from_status?: MiStatus | null
  to_status?: MiStatus | null
  action?: MiAction | null
  action_label?: string | null
  reason?: string | null
  actor: string
  actor_role?: string | null
  detail?: Record<string, unknown> | null
  created_at?: string | null
}

export interface MiItemDetail {
  available: boolean
  item: {
    id: number
    kind: MiKind
    kind_label: string
    title?: string | null
    brand?: string | null
    competitor?: string | null
    category?: string | null
    barcode?: string | null
    yq_item_code?: string | null
    status: MiStatus
    status_label: string
    action?: MiAction | null
    action_label?: string | null
    merged_into?: number | null
    next_statuses: MiStatus[]
  }
  cluster: MiCluster | null
  yq_item: MiYqItem | null
  gap: boolean
  observations: MiObservation[]
  shops?: { shop: string; area?: string | null; sightings: number }[]
  ai_suggestion?: { suggestion: Record<string, unknown>; confidence?: number | null; verified: boolean; label: string } | null
  decisions?: MiDecision[]
  capabilities: MiCaps
}

export interface MiReview {
  available: boolean
  hint?: string
  new_items: MiCluster[]
  unassigned: MiObservation[]
  count: number
  offset?: number
  limit?: number
  capabilities: MiCaps
}

export interface MiMine {
  available: boolean
  hint?: string
  observations: MiObservation[]
  capabilities: MiCaps
}

/* ───────────────────────── v2 (release r8m): lists, products, cases, supplier reports ───────────────────────── */

export type MiPhotoRole = 'product' | 'packaging' | 'label_batch' | 'receipt' | 'other'
export type MiIssueKind = 'quality' | 'packaging' | 'demand' | 'competitor' | 'other'
/** which extra fields a capture shows for an issue type (market_issue_types.fields) */
export type MiIssueField = 'qty' | 'batch_ref' | 'packaging_version' | 'label_photo'
export type MiReportBasis = 'customer_said' | 'rep_saw'

export const ISSUE_KINDS: { value: MiIssueKind; label: string }[] = [
  { value: 'quality', label: 'Quality' },
  { value: 'packaging', label: 'Packaging' },
  { value: 'demand', label: 'Demand' },
  { value: 'competitor', label: 'Competitor' },
  { value: 'other', label: 'Other' },
]
export const ISSUE_FIELDS: { value: MiIssueField; label: string }[] = [
  { value: 'qty', label: 'Quantity affected' },
  { value: 'batch_ref', label: 'Batch / lot code' },
  { value: 'packaging_version', label: 'Packaging version' },
  { value: 'label_photo', label: 'Label / batch photo' },
]
export const REPORT_BASIS_LABEL: Record<MiReportBasis, string> = { customer_said: 'Customer said', rep_saw: 'Rep saw it' }
export const PHOTO_ROLE_LABEL: Record<MiPhotoRole, string> = {
  product: 'Product',
  packaging: 'Packaging',
  label_batch: 'Label / batch',
  receipt: 'Receipt',
  other: 'Other',
}

export interface MiIssueType {
  id: number
  kind: MiIssueKind
  code: string
  label_en: string
  label_ar?: string | null
  safety_default: boolean
  fields: string[]
  active: boolean
  sort: number
}

export interface MiSupplier {
  id: number
  name: string
  code?: string | null
  active: boolean
  merged_into?: number | null
}

export interface MiBrand {
  id: number
  name: string
  supplier_id?: number | null
  aliases: string[]
  renamed_from?: string | null
  active: boolean
  merged_into?: number | null
}

/** GET /market-intel/lists (?all=1: an admin, with the inactive rows). Categories come from the catalog's master. */
export interface MiLists {
  available: boolean
  hint?: string
  suppliers: MiSupplier[]
  brands: MiBrand[]
  issue_types: MiIssueType[]
  categories: unknown[]
}

/** One row of the product picker (GET /market-intel/products). */
export interface MiProduct {
  code: string
  name: string
  brand?: string | null
  category?: string | null
  thumb_url?: string | null
  package_thumb_url?: string | null
  /** the shop bought it in the last 180 days */
  at_shop?: boolean | null
}

export type MiCaseStatus =
  | 'submitted'
  | 'under_review'
  | 'more_info_required'
  | 'verified'
  | 'not_substantiated'
  | 'action_requested'
  | 'resolved'
  | 'closed'

export const CASE_STATUSES: MiCaseStatus[] = [
  'submitted',
  'under_review',
  'more_info_required',
  'verified',
  'not_substantiated',
  'action_requested',
  'resolved',
  'closed',
]

/** The server's own words (app/market_quality.py STATUS_LABELS); a payload's status_label wins. */
export const CASE_STATUS_LABEL: Record<MiCaseStatus, string> = {
  submitted: 'Submitted',
  under_review: 'Under review',
  more_info_required: 'More information required',
  verified: 'Verified',
  not_substantiated: 'Not substantiated',
  action_requested: 'Action requested',
  resolved: 'Resolved',
  closed: 'Closed',
}

/**
 * The moves the API allows (contract §3, PATCH /market-intel/cases/{id}); anything else answers 409.
 * The page offers exactly these, so a reviewer never meets a refusal for a move it showed him.
 */
export const CASE_NEXT: Record<MiCaseStatus, readonly MiCaseStatus[]> = {
  submitted: ['under_review', 'more_info_required', 'verified', 'not_substantiated'],
  under_review: ['more_info_required', 'verified', 'not_substantiated'],
  more_info_required: ['under_review', 'verified', 'not_substantiated'],
  verified: ['action_requested', 'closed'],
  not_substantiated: ['closed', 'under_review'],
  action_requested: ['resolved'],
  resolved: ['closed'],
  closed: ['under_review'],
}

/**
 * What one move needs, from the same contract:
 *   • Verified: a method and a reason (the API sets the evidence level to at least 3);
 *   • Not substantiated: a reason; back to review from Not substantiated: a reason;
 *   • Closed → Under review: an admin and a reason;
 *   • More info required: a question to the rep (POST …/question);
 *   • Action requested: a supplier action (POST …/action);
 *   • Resolved: a supplier action that carries the supplier's response AND YQ's resolution check.
 */
export interface CaseMove {
  to: MiCaseStatus
  via: 'patch' | 'question' | 'action'
  reason: 'optional' | 'required'
  method: boolean
  /** why the move cannot be made yet (shown beside the disabled button) */
  blocked: string | null
}

export function caseMoves(from: MiCaseStatus, o: { admin: boolean; actionDone: boolean; allowed?: readonly string[] | null }): CaseMove[] {
  const out: CaseMove[] = []
  for (const to of CASE_NEXT[from] || []) {
    if (from === 'closed' && to === 'under_review' && !o.admin) continue
    // the case page's next_statuses: the moves THIS login may make now (none on a merged case)
    if (o.allowed && !o.allowed.includes(to)) continue
    const via: CaseMove['via'] = to === 'more_info_required' ? 'question' : to === 'action_requested' ? 'action' : 'patch'
    const required =
      to === 'verified' || to === 'not_substantiated' || (from === 'not_substantiated' && to === 'under_review') || (from === 'closed' && to === 'under_review')
    out.push({
      to,
      via,
      reason: required ? 'required' : 'optional',
      method: to === 'verified',
      blocked: to === 'resolved' && !o.actionDone ? "Record the supplier's response and YQ's resolution check on an action first." : null,
    })
  }
  return out
}

/** 1 reported → 5 resolution verified; the level only ever moves up. */
export const EVIDENCE_LEVELS: { level: number; code: string; label: string }[] = [
  { level: 1, code: 'reported', label: 'Reported' },
  { level: 2, code: 'evidence_submitted', label: 'Evidence submitted' },
  { level: 3, code: 'inspected_verified', label: 'Inspected & verified by YQ' },
  { level: 4, code: 'supplier_confirmed', label: 'Supplier confirmed' },
  { level: 5, code: 'resolution_verified', label: 'Resolution verified' },
]

export function evidenceLabel(level?: number | null): string {
  return EVIDENCE_LEVELS.find((e) => e.level === Number(level))?.label || 'Reported'
}

/** app/market_quality.py VERIFICATION_LABELS; the case's verification_label wins. */
export const VERIFY_METHODS: { value: string; label: string }[] = [
  { value: 'sample_inspected', label: 'Sample inspected' },
  { value: 'photos_sufficient', label: 'Photos judged sufficient' },
  { value: 'several_shops', label: 'Reported by several shops' },
]

export type MiNamed = string | { id?: number | null; name?: string | null } | null | undefined

/** A brand / supplier the API sent as a name or as {id, name}. */
export function nameOf(v: MiNamed): string {
  if (!v) return ''
  return typeof v === 'string' ? v : String(v.name || '')
}

export interface MiCaseRow {
  id: number
  /** a row that is a sighting rather than a case (the "Photos to identify" tab may send those) */
  case_id?: number | null
  observation_id?: number | null
  title: string
  status: MiCaseStatus
  /** the server's words for the status and the evidence level (preferred over the client copies) */
  status_label?: string | null
  evidence_level: number
  evidence_label?: string | null
  safety: boolean
  severity?: number | null
  issue_type?: { id: number; code?: string | null; label_en?: string | null; label_ar?: string | null } | null
  yq_item_code?: string | null
  product_name?: string | null
  brand?: MiNamed
  brand_id?: number | null
  supplier?: MiNamed
  supplier_id?: number | null
  obs_count?: number | null
  shop_count?: number | null
  first_seen?: string | null
  last_seen?: string | null
  thumb_url?: string | null
  due_date?: string | null
  assignee?: string | null
  merged_into?: number | null
}

export type MiCaseTab = 'safety' | 'new' | 'need_info' | 'identify' | 'all'

export interface MiCaseList {
  available?: boolean
  hint?: string
  rows: MiCaseRow[]
  count: number
  tabs?: Partial<Record<Exclude<MiCaseTab, 'all'>, number>> | null
  capabilities?: MiCaps
}

export interface MiCase extends MiCaseRow {
  issue_type_id?: number | null
  batch_ref?: string | null
  packaging_version?: string | null
  verification_method?: string | null
  verification_label?: string | null
  verification_reason?: string | null
  status_reason?: string | null
  changed_at?: string | null
  changed_by?: string | null
  created_at?: string | null
  /** the linked catalog item (display_name, thumb) when the case has a YQ code */
  yq_item?: MiYqItem | null
  /** the moves this login may make now (the server's NEXT_STATUS for a reviewer; empty otherwise) */
  next_statuses?: MiCaseStatus[] | null
}

/** The case's product line: "C-10 · Brand X charger" (the catalog name, else what the payload carries). */
export function caseProduct(c: Pick<MiCase, 'yq_item_code' | 'yq_item' | 'product_name'>): string {
  if (!c.yq_item_code) return ''
  return [c.yq_item_code, c.yq_item?.display_name || c.product_name].filter(Boolean).join(' · ')
}

/**
 * Where the safety e-mail stands, from the case's 'safety_alert' events: the claim is written first
 * (detail.email "queued"), the outcome after the answer ("sent", or "not_sent" with a reason). The
 * latest outcome wins; only a "sent" outcome may say the manager was e-mailed.
 */
export function safetyMail(events: MiCaseEvent[] | null | undefined): { state: 'sent' | 'not_sent' | 'queued' | null; reason: string | null } {
  let state: 'sent' | 'not_sent' | 'queued' | null = null
  let reason: string | null = null
  const ordered = [...(events || [])].filter((e) => e.event === 'safety_alert').sort((a, b) => Number(a.id) - Number(b.id))
  for (const e of ordered) {
    const mail = String((e.detail || {}).email || '')
    if (mail === 'sent') {
      state = 'sent'
      reason = null
    } else if (mail === 'not_sent') {
      state = 'not_sent'
      reason = typeof e.detail?.reason === 'string' ? e.detail.reason : null
    } else if (mail === 'queued' && state === null) {
      state = 'queued'
    }
  }
  return { state, reason }
}

export interface MiCaseEvent {
  id: number
  at?: string | null
  actor?: string | null
  event: string
  from_status?: string | null
  to_status?: string | null
  detail?: Record<string, unknown> | null
}

export type MiActionStatus = 'open' | 'responded' | 'resolved' | 'cancelled'
export const ACTION_STATUS_LABEL: Record<MiActionStatus, string> = {
  open: 'Open',
  responded: 'Supplier responded',
  resolved: 'Resolved',
  cancelled: 'Cancelled',
}

export interface MiSupplierAction {
  id: number
  supplier_id?: number | null
  supplier?: MiNamed
  request: string
  owner?: string | null
  due_date?: string | null
  status: MiActionStatus
  supplier_response?: string | null
  resolution_check?: string | null
  created_at?: string | null
  updated_at?: string | null
  days_open?: number | null
}

/** GET /market-intel/cases/{id}: the case, its timeline, its sightings (with photos), actions and merge suggestions. */
export interface MiCaseDetail {
  available?: boolean
  hint?: string
  case?: MiCase
  events?: MiCaseEvent[]
  observations?: MiObservation[]
  actions?: MiSupplierAction[]
  suggestions?: MiCaseRow[]
  capabilities?: MiCaps
}

/** The case itself, whether the API nests it under `case` or answers it flat. */
export function caseOf(d: MiCaseDetail | null | undefined): MiCase | null {
  if (!d) return null
  if (d.case) return d.case
  const flat = d as unknown as MiCase
  return typeof flat.id === 'number' && typeof flat.status === 'string' ? flat : null
}

export type MiReportStatus = 'draft' | 'approved' | 'superseded'
export type MiEdition = 'supplier' | 'internal'

/** app/supplier_reports.py _report_out: one report (the list sends `downloads` as a number). */
export interface MiReport {
  id: number
  supplier_id: number
  supplier?: MiNamed
  supplier_name?: string | null
  /** 'YYYY-MM' (an older payload: the first day 'YYYY-MM-01') */
  month: string
  month_label?: string | null
  edition: MiEdition
  status: MiReportStatus
  narrative?: string | null
  narrative_source?: 'template' | 'edited' | string | null
  included_observation_ids?: number[] | null
  insufficient_observation_ids?: number[] | null
  counts?: Record<string, unknown> | null
  approved_by?: string | null
  approved_at?: string | null
  file_sha256?: string | null
  downloads?: { at?: string | null; by?: string | null; sha256?: string | null }[] | number | null
  created_at?: string | null
  created_by?: string | null
  updated_at?: string | null
}

export type MiReportRowState = 'included' | 'insufficient' | 'excluded'

/**
 * One row of a report preview (supplier_reports._finding_row / _market_row): a quality or packaging
 * finding, or (internal edition) a competitor / demand sighting. `state` says where it stands: in the
 * report, listed as insufficient evidence (not counted), or left out by hand.
 */
export interface MiReportRow {
  id: number
  ref?: string | null
  date?: string | null
  kind?: string | null
  sku?: string | null
  product?: string | null
  /** an object {id, code, label}: render issue.label, never the object */
  issue?: { id?: number | null; code?: string | null; label?: string | null } | string | null
  description?: string | null
  qty?: number | null
  batch_ref?: string | null
  packaging_version?: string | null
  basis?: string | null
  evidence_level?: number | null
  evidence_label?: string | null
  /** "Verified" | "Unverified report" */
  finding?: string | null
  status?: string | null
  case_id?: number | null
  recurring?: boolean | null
  safety?: boolean | null
  photos_withheld?: number | null
  state?: MiReportRowState | null
  photos?: { ref?: string | null; id?: number | null; role?: string | null; role_words?: string | null; caption?: string | null; thumb_url?: string | null }[] | null
  /* internal edition: competitor and demand rows */
  note?: string | null
  area?: string | null
  product_seen?: string | null
  brand?: string | null
  competitor?: string | null
  category?: string | null
  price_seen?: string | null
  source?: string | null
  asked_for?: string | null
  demand_level?: string | null
}

/** The report routes' own capabilities (build / edit: reviewers; approve: approvers the gate lets write). */
export interface MiReportCaps {
  can_build?: boolean
  can_edit?: boolean
  can_approve?: boolean
  can_download?: boolean
}

/** GET / POST / PATCH a report: the report, its rows by sheet, its actions and what the data says now. */
export interface MiReportDetail {
  available?: boolean
  report?: MiReport
  /** keyed by sheet: product_issues, packaging (+ competitor, demand in the internal edition) */
  rows?: Record<string, MiReportRow[]> | MiReportRow[] | null
  actions?: { id: number; ref?: string | null; request?: string | null; status?: string | null; status_words?: string | null; due_date?: string | null; days_open?: number | null }[] | null
  live_counts?: Record<string, unknown> | null
  warnings?: string[] | null
  /** build only: an existing draft of the same supplier, month and edition was rebuilt (hand edits replaced) */
  rebuilt?: boolean | null
  capabilities?: MiReportCaps | null
}

export const REPORT_SHEET_LABEL: Record<string, string> = {
  product_issues: 'Product issues',
  packaging: 'Packaging',
  competitor: 'Competitor',
  demand: 'Demand',
}

/** Every row of a preview with its sheet, whichever way the payload groups them. */
export function reportRows(d: Pick<MiReportDetail, 'rows'> | null | undefined): { sheet: string; row: MiReportRow }[] {
  const rows = d?.rows
  if (!rows) return []
  if (Array.isArray(rows)) return rows.map((row) => ({ sheet: row.kind === 'packaging' ? 'packaging' : 'product_issues', row }))
  const out: { sheet: string; row: MiReportRow }[] = []
  for (const [sheet, list] of Object.entries(rows)) {
    if (Array.isArray(list)) for (const row of list) out.push({ sheet, row })
  }
  return out
}

/** The issue's words on a report row (an object from the API; a plain string from an older one). */
export function reportIssue(row: Pick<MiReportRow, 'issue'>): string {
  const i = row.issue
  if (!i) return ''
  return typeof i === 'string' ? i : String(i.label || i.code || '')
}

/**
 * A route of this release answering 503 means its tables are not migrated yet (the API is newer than
 * the database); a 404 from a LIST route means the API itself is older than this page. Either way
 * the tab shows a calm "Not available yet" panel instead of an error.
 */
export function notReady(e: unknown, missingRouteToo = true): boolean {
  return e instanceof ApiError && (e.status === 503 || (missingRouteToo && e.status === 404))
}

/** The lists a capture and the office read (active only; `all`: an admin, inactive rows too). */
export async function fetchLists(all = false): Promise<MiLists> {
  const d = await apiGet<Partial<MiLists> | null>(`/market-intel/lists${all ? '?all=1' : ''}`)
  return {
    available: d?.available !== false,
    hint: d?.hint,
    suppliers: d?.suppliers || [],
    brands: (d?.brands || []).map((b) => ({ ...b, aliases: b.aliases || [] })),
    issue_types: (d?.issue_types || []).map((t) => ({ ...t, fields: t.fields || [] })),
    categories: d?.categories || [],
  }
}

/** The issue types a capture tile offers (active, the admin's order). */
export function issueTypesFor(lists: MiLists | null | undefined, kind: MiIssueKind): MiIssueType[] {
  return (lists?.issue_types || [])
    .filter((t) => t.active !== false && t.kind === kind)
    .sort((a, b) => (a.sort ?? 100) - (b.sort ?? 100) || a.label_en.localeCompare(b.label_en))
}

/** Category names from the catalog master, whether the API sends strings or {name|label|code} rows. */
export function categoryNames(lists: MiLists | null | undefined): string[] {
  const out: string[] = []
  for (const c of lists?.categories || []) {
    if (typeof c === 'string') out.push(c)
    else if (c && typeof c === 'object') {
      const r = c as { name?: unknown; label?: unknown; code?: unknown; category?: unknown }
      const v = r.name ?? r.label ?? r.category ?? r.code
      if (typeof v === 'string' && v) out.push(v)
    }
  }
  return out
}

/** The admin's brand for a name typed or carried by a product: its name or one of its aliases. */
export function matchBrand(lists: MiLists | null | undefined, text?: string | null): MiBrand | null {
  const t = String(text || '').trim().toLowerCase()
  if (!t || !lists) return null
  return (
    lists.brands.find((b) => b.active !== false && !b.merged_into && (b.name.toLowerCase() === t || (b.aliases || []).some((a) => a.toLowerCase() === t))) || null
  )
}

/** The shop's phone digits: the only way the products route knows the shop (no id in the rep's book). */
export function shopPhone(shop: { phone?: string | null } | null | undefined): string {
  return String(shop?.phone || '').replace(/\D/g, '')
}

/** One cache entry per shop and query, shared by the picker and the sheet's warm-up when it opens. */
export function productsQueryKey(shop: { phone?: string | null } | null | undefined, q: string) {
  return ['mi-products', shopPhone(shop), q] as const
}

/**
 * The product picker's rows. An Arabic query is read into the catalog's English words first (the
 * marketplace's own reader, loaded only when Arabic is typed). The shop the rep stands in travels as
 * its phone (`shop_phone`: the route resolves the shop from it; the staff book carries no shop id).
 */
export async function fetchProducts(q: string, shop?: { phone?: string | null } | null, limit = 20): Promise<MiProduct[]> {
  let query = q.trim()
  if (/[؀-ۿ]/.test(query)) {
    try {
      const m = await import('@/market/lib/search')
      query = m.queryText(query).trim() || query
    } catch {
      /* keep the words as typed */
    }
  }
  const p = new URLSearchParams({ limit: String(limit) })
  if (query) p.set('q', query.slice(0, 80))
  const phone = shopPhone(shop)
  if (phone) p.set('shop_phone', phone)
  const d = await apiGet<MiProduct[] | { items?: MiProduct[]; products?: MiProduct[]; rows?: MiProduct[] } | null>(`/market-intel/products?${p.toString()}`)
  const rows = Array.isArray(d) ? d : d?.items || d?.products || d?.rows || []
  return rows.filter((r) => r && r.code)
}

/* the products this phone picked lately ("Recent" in the picker): a convenience, never needed */
const RECENT_KEY = 'yq-mi-recent-products'

export function readRecentProducts(): MiProduct[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') as unknown
    return Array.isArray(v) ? (v as MiProduct[]).filter((p) => p && typeof p.code === 'string' && typeof p.name === 'string').slice(0, 8) : []
  } catch {
    return []
  }
}

export function rememberProduct(p: MiProduct): void {
  try {
    const keep = { code: p.code, name: p.name, brand: p.brand ?? null, category: p.category ?? null, thumb_url: p.thumb_url ?? null, package_thumb_url: p.package_thumb_url ?? null }
    const next = [keep, ...readRecentProducts().filter((x) => x.code !== p.code)].slice(0, 8)
    localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    /* storage blocked: Recent stays empty */
  }
}

/**
 * When the sighting happened: the first photo's own time (the phone stamps the file as the camera
 * hands it over), else when Spotted was tapped. It is fixed at Save, so a capture that waits in the
 * queue for signal still says when the rep was in the shop; the server keeps it only when it falls
 * within the last 7 days and not ahead of its clock (else its own time).
 */
export function observedAtIso(photos: Pick<ReadyPhoto, 'takenAt'>[], openedAt: number, now: number = Date.now()): string {
  const ok = (t?: number | null): t is number => typeof t === 'number' && Number.isFinite(t) && t > 0 && t <= now + 10 * 60_000
  const shot = photos.map((p) => p.takenAt).find(ok)
  return new Date(shot ?? (ok(openedAt) ? openedAt : now)).toISOString()
}

/** `photo_roles` for the capture: one role per photo, in the order the photos are sent. */
export function photoRoles(photos: Pick<ReadyPhoto, 'role'>[]): string {
  return photos.map((p) => p.role || 'product').join(',')
}

/** "2026-09" (or "2026-09-01") -> "September 2026" */
export function monthText(m?: string | null): string {
  const s = String(m || '')
  const y = Number(s.slice(0, 4))
  const mo = Number(s.slice(5, 7))
  if (!y || !mo) return s
  return new Date(y, mo - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
}

/** The month before `now`, as 'YYYY-MM' (the supplier report builder's default). */
export function previousMonth(now: Date): string {
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/* ───────────────────────── small helpers ───────────────────────── */

export function newUuid(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch {
    /* fall through */
  }
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** "2.500" / "2.500–3.000" with the currency, from the API's 3-dp strings. */
export function priceRange(min?: string | null, max?: string | null): string | null {
  if (!min) return null
  return max && max !== min ? `BHD ${min}–${max}` : `BHD ${min}`
}

export function shortDate(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/* ───────────────────────── photo resize (on the phone) ───────────────────────── */

export const MAX_PHOTOS = 4
const MAX_EDGE = 1600
const QUALITY = 0.75

export interface ReadyPhoto {
  blob: Blob
  name: string
  url: string
  /** v2: what the photo shows (sent as photo_roles); a plain capture photo is 'product' */
  role?: MiPhotoRole
  /** v2: when the phone made the file (its lastModified), for client_observed_at */
  takenAt?: number
}

async function decode(file: File): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file, { imageOrientation: 'from-image' })
    } catch {
      /* fall back to an <img> decode below */
    }
  }
  const url = URL.createObjectURL(file)
  try {
    const img = new Image()
    img.decoding = 'async'
    img.src = url
    await img.decode()
    return img
  } finally {
    URL.revokeObjectURL(url)
  }
}

function toBlob(canvas: HTMLCanvasElement, type: string): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), type, QUALITY))
}

/**
 * Longest side 1600 px, WebP (JPEG where the browser cannot write WebP), quality 0.75: about
 * 300 KB. Drawing onto a canvas keeps pixels only, so the phone's EXIF (GPS, device) never leaves
 * it; the server strips again anyway. A photo the browser cannot decode (HEIC outside Safari) is
 * sent as it is when it is a JPEG / PNG / WEBP, and refused with a plain reason otherwise.
 */
export async function preparePhoto(file: File, index: number): Promise<ReadyPhoto> {
  const takenAt = Number.isFinite(file.lastModified) && file.lastModified > 0 ? file.lastModified : undefined
  let src: ImageBitmap | HTMLImageElement
  try {
    src = await decode(file)
  } catch {
    if (/^image\/(jpeg|png|webp)$/.test(file.type)) {
      const ext = file.type === 'image/png' ? 'png' : file.type === 'image/webp' ? 'webp' : 'jpg'
      return { blob: file, name: `spotted-${index + 1}.${ext}`, url: URL.createObjectURL(file), takenAt }
    }
    throw new Error("This photo type can't be read here. Take it with the camera button instead.")
  }
  const w = 'naturalWidth' in src ? src.naturalWidth : src.width
  const h = 'naturalHeight' in src ? src.naturalHeight : src.height
  const scale = Math.min(1, MAX_EDGE / Math.max(w, h, 1))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(w * scale))
  canvas.height = Math.max(1, Math.round(h * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Could not prepare the photo on this phone.')
  ctx.drawImage(src, 0, 0, canvas.width, canvas.height)
  if ('close' in src && typeof src.close === 'function') src.close()
  let blob = await toBlob(canvas, 'image/webp')
  let ext = 'webp'
  if (!blob || blob.type !== 'image/webp') {
    blob = await toBlob(canvas, 'image/jpeg')
    ext = 'jpg'
  }
  if (!blob) throw new Error('Could not prepare the photo on this phone.')
  return { blob, name: `spotted-${index + 1}.${ext}`, url: URL.createObjectURL(blob), takenAt }
}

/* ───────────────────────── the in-page retry queue ───────────────────────── */

export interface CaptureDraft {
  client_uuid: string
  fields: Record<string, string>
  photos: ReadyPhoto[]
}

export interface QueuedCapture extends CaptureDraft {
  state: 'sending' | 'waiting' | 'failed'
  error?: string
  tries: number
}

const RETRY_MS = 20_000
let queue: QueuedCapture[] = []
const listeners = new Set<() => void>()
let timer: number | undefined

function emit() {
  queue = [...queue]
  listeners.forEach((fn) => fn())
}

function patch(id: string, p: Partial<QueuedCapture>) {
  queue = queue.map((q) => (q.client_uuid === id ? { ...q, ...p } : q))
  emit()
}

function drop(id: string, revoke = true) {
  const gone = queue.find((q) => q.client_uuid === id)
  if (revoke) gone?.photos.forEach((p) => URL.revokeObjectURL(p.url))
  queue = queue.filter((q) => q.client_uuid !== id)
  emit()
}

/** Worth retrying: no answer at all, a timeout, or the server / its host being unwell. */
function retryable(e: unknown): boolean {
  if (isNetworkError(e)) return true
  if (e && typeof e === 'object' && (e as { name?: unknown }).name === 'AbortError') return true
  return e instanceof ApiError && (e.status >= 500 || e.status === 408 || e.status === 429)
}

async function send(d: CaptureDraft): Promise<MiCard> {
  const form = new FormData()
  form.append('client_uuid', d.client_uuid)
  for (const [k, v] of Object.entries(d.fields)) if (v !== '') form.append(k, v)
  d.photos.forEach((p) => form.append('photos', p.blob, p.name))
  return apiUpload<MiCard>('/market-intel/observations', form)
}

function schedule() {
  if (timer !== undefined || typeof window === 'undefined') return
  timer = window.setTimeout(() => {
    timer = undefined
    void retryWaiting()
  }, RETRY_MS)
}

async function attempt(id: string): Promise<MiCard | null> {
  const q = queue.find((x) => x.client_uuid === id)
  if (!q) return null
  patch(id, { state: 'sending', error: undefined, tries: q.tries + 1 })
  try {
    const card = await send(q)
    drop(id)
    return card
  } catch (e) {
    if (retryable(e)) {
      patch(id, { state: 'waiting', error: 'No connection. Kept on this phone; trying again.' })
      schedule()
      return null
    }
    let msg = 'The server refused this sighting.'
    if (e instanceof ApiError) msg = detailText(e.body) || msg // its detail, or a 422's first line
    patch(id, { state: 'failed', error: msg })
    throw new Error(msg, { cause: e })
  }
}

/**
 * Send one capture. Resolves with the result card, or with null when the capture was queued (no
 * connection: it is retried every 20 s and when the phone comes back online, while the app is
 * open). Rejects with the server's reason for a capture it refused (a bad price, a bad photo).
 */
export async function submitCapture(d: CaptureDraft): Promise<MiCard | null> {
  queue = [...queue.filter((q) => q.client_uuid !== d.client_uuid), { ...d, state: 'sending', tries: 0 }]
  emit()
  return attempt(d.client_uuid)
}

export async function retryWaiting(): Promise<void> {
  for (const q of queue.filter((x) => x.state === 'waiting')) {
    try {
      await attempt(q.client_uuid)
    } catch {
      /* marked failed; the sheet shows it */
    }
  }
}

/** Forget a queued capture. `keepPhotos`: the sheet still shows its photos (a refused save it will fix). */
export function discardCapture(id: string, keepPhotos = false) {
  drop(id, !keepPhotos)
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => void retryWaiting())
}

export function useCaptureQueue(): QueuedCapture[] {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => queue,
    () => queue,
  )
}
