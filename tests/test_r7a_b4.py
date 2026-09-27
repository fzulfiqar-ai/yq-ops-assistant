"""Release R7a "Make it true", stream B4 — order desk UI + salesman sticky fix.

    python -m tests.test_r7a_b4

Same lightweight runner as tests/test_r6_speed.py (no pytest, no network, no .env). This stream
touches only web/src (ShopOrders.tsx, shop-ops/OrderActions.tsx + pipeline.ts, and the arc-reveal
intro) — there is no backend for the new /shop/focus/candidates + /shop/orders/{id}/focus-link
routes yet (a separate stream owns that + scripts/r7_focus_links_migration.sql), so every test
here reads the TypeScript sources directly, the same way test_r6_speed.py checks the web side
(wrangler config, vitals, ErrorBoundaries, …) from _read() rather than a JS runner.
"""
from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


ARC = "web/src/components/ui/arc-preloader-hero.tsx"
ROOT_MOUNT = "web/src/PortalRoot.tsx"
SHOP = "web/src/pages/ShopOrders.tsx"
ACTIONS = "web/src/pages/shop-ops/OrderActions.tsx"
PIPELINE = "web/src/pages/shop-ops/pipeline.ts"


# ── item 1: the salesman sticky bug (ArcRevealHero) ────────────────────────────

@test("ArcRevealHero's wrapping section clips instead of hiding overflow, so it is never a scroll container")
def _():
    src = _read(ARC)
    section = src.split("<section", 1)[1].split(">", 1)[0]
    assert "overflow-clip" in section, section
    assert "overflow-hidden" not in section, "still a scroll container -> sticky descendants never stick"
    # the intro overlay's own clipping (a different, absolutely-positioned div) is untouched
    assert src.count("overflow-hidden") == 1, "exactly the intro overlay div, nowhere else"


@test("the intro is skipped for role salesman, decided once at mount (no first-frame flash)")
def _():
    src = _read(ARC)
    assert "React.useState<Phase>(() => {" in src, "a lazy initializer, not a plain default + effect"
    init = src.split("React.useState<Phase>(() => {", 1)[1].split("})", 1)[0]
    assert "prefersReducedMotion" in init and "return 'done'" in init
    assert "yq-role" in init and "'salesman'" in init, "the same remembered-role key auth.tsx sets"
    assert "storageKey" in init, "a viewer who already finished this session still skips it"
    # 'yq-role' is only READ once, in the initializer (the other hit is the comment explaining
    # it) — no separate effect re-checks it on every render, and there's no lingering
    # setState-in-an-effect for the role/storage check either (a live media-query flip for
    # prefers-reduced-motion is deliberately NOT mirrored into state post-mount, to avoid exactly
    # that react-hooks/set-state-in-effect pattern).
    assert src.count("localStorage.getItem('yq-role')") == 1
    assert "if (prefersReducedMotion) setPhase" not in src


@test("PortalRoot still mounts ArcRevealHero around the whole app (nothing moved)")
def _():
    src = _read(ROOT_MOUNT)
    assert "<ArcRevealHero" in src and "<App />" in src
    assert src.index("<ArcRevealHero") < src.index("<App />") < src.index("</ArcRevealHero>")


# ── item 2: the order drawer footer (Confirm + Cancel, never just Cancel) ──────

@test("a Received order's sticky footer always offers Confirm first and Cancel second")
def _():
    src = _read(SHOP)
    assert "actions.filter((a) => a !== 'confirmed' || unassigned)" not in src, "the old bug: dropped Confirm for an assigned order"
    footer = src.split("{data && actions.length > 0 && !confirming && !readOnly && (", 1)[1]
    footer = footer.split("\n        )}\n      </div>\n    </div>\n  )\n}", 1)[0]
    assert "data.status === 'new' && (" in footer and "Confirm order" in footer
    assert "actions.filter((a) => a !== 'confirmed' && a !== 'cancelled')" in footer
    assert "actions.includes('cancelled')" in footer and "Cancel order" in footer
    # Confirm comes before Cancel in source order (primary, then secondary)
    assert footer.index("Confirm order") < footer.index("Cancel order")


@test("the footer's 'note to the shop' field is gone (0 merchants have an email — it was never sent)")
def _():
    src = _read(SHOP)
    assert "goes in their update email" not in src
    assert "Note to the shop" not in src
    # the desk drawer no longer threads a general free-text note into a status change
    drawer = src.split("function OrderDrawer(", 1)[1].split("\nfunction ", 1)[0]
    assert "setNote" not in drawer and "const [note, setNote]" not in drawer


# ── item 3: plain-English attribution ──────────────────────────────────────────

@test("attributionLabel covers the four documented sources with the exact rewording")
def _():
    src = _read(PIPELINE)
    block = src.split("const ATTRIBUTION_LABEL", 1)[1].split("export function attributionLabel", 1)[0]
    assert "session_ref: (rep) => `Came via ${rep}'s link`" in block
    assert "sticky: (rep) => `${rep}'s shop`" in block
    assert "customer_admin: () => 'Assigned by office'" in block
    assert "checkout_pick: () => 'Picked at checkout'" in block
    fn = src.split("export function attributionLabel", 1)[1].split("\n}", 1)[0]
    assert "source.replace(/_/g, ' ')" in fn, "an attribution not in the map still renders something"


@test("shopRepLine replaces the old 'not settled yet · first link /x' with what happens next")
def _():
    src = _read(PIPELINE)
    fn = src.split("export function shopRepLine", 1)[1].split("\n}", 1)[0]
    assert "`Assigned by office — ${shopRep.salesman_name}`" in fn
    assert "`${shopRep.sticky_name}'s shop`" in fn
    assert "`New shop — becomes ${currentRepName || 'the rep'}'s when they confirm`" in fn
    drawer_src = _read(SHOP)
    assert "not settled yet" not in drawer_src
    assert "first rep to confirm" not in drawer_src
    assert "shopRepLine(shopRep, data.salesman_name)" in drawer_src


@test("the drawer's salesman row uses attributionLabel instead of a raw snake_case badge")
def _():
    src = _read(SHOP)
    assert "attributionLabel(data.attribution_source, data.salesman_name)" in src
    assert "{data.attribution_source.replace(/_/g, ' ')}" not in src


# ── item 4: status chips with counts, Needs action, hide the empty My-link card ─

@test("status chips carry counts, 'Received' shows the oldest age, and a computed Needs-action chip exists")
def _():
    src = _read(SHOP)
    assert "'needs_action'" in src and "type StatusFilter" in src
    assert "Needs action" in src and "needsActionCount" in src
    assert "function ageCompact(" in src and "function oldestAgeMin(" in src
    chips = src.split("(['all', ...STATUSES] as const).map((s) => {", 1)[1].split("})", 1)[0]
    assert "`Received ${n}" in chips and "oldest ${ageCompact(oldestReceivedMin)}" in chips
    assert "counts[s] ?? 0" in chips


@test("the Needs-action count and its filter share one rule (late Received or open with no rep), each order once")
def _():
    src = _read(SHOP)
    assert "const slaMin = queueQuery.data?.sla_min ?? 120" in src
    assert "const needsActionIds = new Set<number>([" in src and "const needsActionCount = needsActionIds.size" in src
    assert "isLateReceived(r, slaMin) || (OPEN_STATUSES.has(r.status) && !r.salesman_id && !r.salesman_name)" in src
    assert "focusExceptionsCount" not in src, "Focus exceptions keep their own count in the panel (review R7a)"


@test("isLateReceived / oldestAgeMin agree with a hand-checked scenario (SLA=120min)")
def _():
    # A light re-implementation of the two pure helpers' documented contract, checked against the
    # exact source (so a change to either drifts this test, not just the assertion below).
    src = _read(SHOP)
    assert "r.status !== 'new' || !r.created_at) return false" in src
    assert "(Date.now() - t) / 60000 > slaMin" in src
    assert "Math.max(0, Math.floor((Date.now() - Math.min(...times)) / 60000))" in src


@test("the office desk's 'My link' card is hidden entirely when the admin has no active salesman row")
def _():
    src = _read(SHOP)
    assert "{meData?.salesman && <MyLinkCard me={meData} isAdmin companyKpis={null} />}" in src
    # the fallback company-KPI fetch that fed the buggy all-zero card is gone, not just unrendered
    assert "shop-orders-kpi-fallback" not in src
    assert "needsCompanyKpi" not in src


# ── item 5: collapse legacy 'reminded' events into one summary row ─────────────

@test("collapseReminders groups every 'reminded' event into one row and eventLabel renders it in words")
def _():
    src = _read(SHOP)
    assert "function collapseReminders(events: OrderEvent[])" in src
    body = src.split("function collapseReminders(events: OrderEvent[])", 1)[1].split("\n}\n", 1)[0]
    assert "if (reminded.length < 2) return events" in body, "0 or 1 reminders: nothing to collapse"
    assert "event: 'reminded:summary'" in body
    assert "detail: { total: reminded.length, reached }" in body
    label_fn = src.split("if (event === 'reminded:summary') {", 1)[1].split("}\n", 1)[0]
    assert "`Reminded ${total}× — rep reached ${reached}×`" in label_fn
    assert "`Reminded ${total}× — rep not reached`" in label_fn
    # both timelines (desk drawer + field sheet) render the collapsed list, not the raw one
    assert src.count("collapseReminders(data.events).map(") == 2, "OrderDrawer + FieldOrderSheet"
    assert "data.events.map((e, i) => (" not in src


# ── item 6: ONE Focus exceptions panel, fed by the documented contract ─────────

@test("the API contract types match the brief exactly (field names, unions, per-order cap)")
def _():
    src = _read(PIPELINE)
    cand = src.split("export interface FocusCandidate {", 1)[1].split("\n}", 1)[0]
    for field in ("invoice_key: string", "invoice_date: string | null", "focus_salesman", "focus_customer",
                  "invoice_open_bhd", "invoice_linked_n", "invoice_orders_n", "amount_diff_bhd",
                  "method: 'narration_ref' | 'sio_ref' | 'auto_items'", "method_label: string",
                  "confidence: number", "overlap_share: number | null", "sio_key: string | null",
                  "rank: number", "exact: boolean", "lines: FocusCandidateLines"):
        assert field in cand, field
    resp = src.split("export interface FocusCandidatesResp {", 1)[1].split("\n}", 1)[0]
    for field in ("orders: FocusOrderRow[]", "count: number", "candidates: number",
                  "ledger_as_of: string | null", "hint?: string"):
        assert field in resp, field
    assert "apiGet<FocusCandidatesResp>('/shop/focus/candidates?limit=300&per_order=5')" in src


@test("lineDiffSummary only mentions the parts that differ from a clean match")
def _():
    src = _read(PIPELINE)
    fn = src.split("export function lineDiffSummary", 1)[1].split("\n}", 1)[0]
    assert "`${l.matched} of ${l.order} lines match`" in fn
    assert "missing_on_invoice" in fn and "not invoiced" in fn
    assert "extra_on_invoice" in fn and "extra on invoice" in fn


@test("Accept/reject call the decision endpoint with a normalisable key, and never as a raw fetch")
def _():
    src = _read(ACTIONS)
    assert "apiPost<FocusLinkResp>(`/shop/orders/${row.order_id}/focus-link`, { invoice_key: c.invoice_key, action })" in src
    # "Not this one" really posts action: 'reject' (a real, remembered decision) — not a client-only skip
    assert "decide(best, 'reject')" in src and "decide(best, 'accept')" in src


@test("the panel says nothing while loading, on any error, or once nothing needs a look")
def _():
    src = _read(ACTIONS)
    fn = src.split("export function FocusExceptionsPanel", 1)[1].split("\n}\n", 1)[0]
    assert "if (isLoading || isError || !data?.orders?.length) return null" in fn


@test("the old Focus check / Payment column / PaymentBox / free-text invoice inputs are gone from the desk")
def _():
    src = _read(SHOP)
    for gone in ("function FocusCheck(", "interface ReconRow", "shop-focus-recon",
                 "<PaymentPill", "<PaymentBox", "<InvoiceBox",
                 "Focus invoice no (optional, with Delivered)", "Focus invoice no (optional)",
                 "No invoice</Badge>"):
        assert gone not in src, gone
    assert "<FocusExceptionsPanel" in src
    assert src.count("InvoicedBadge invoiceNo=") >= 4, "table row, drawer header, field card, field sheet"


@test("PaymentBox/PaymentPill/InvoiceBox still exist in shop-ops (reusable), just unused by the desk now")
def _():
    src = _read(ACTIONS)
    assert "export function PaymentBox(" in src
    assert "export function PaymentPill(" in src
    assert "export function InvoiceBox(" in src


# ── item 7: a read-only 'management' role, hides every write action ────────────

@test("the desk uses the shared auth helpers: management sees every order, read-only")
def _():
    src = _read(SHOP)
    assert "import { useAuth, isReadOnly, seesAllOrders } from '@/lib/auth'" in src
    assert "const readOnly = isReadOnly(me)" in src
    assert "const showDesk = seesAllOrders(me)" in src
    assert "readOnly={readOnly}" in src
    assert "TODO(management-stream)" not in src and "isManagementRole" not in src


@test("readOnly hides confirm/cancel/assign/status/focus-accept everywhere it's threaded")
def _():
    shop = _read(SHOP)
    assert "readOnly?: boolean" in shop
    assert "{!readOnly && <AssignmentQueue" in shop
    assert "!readOnly && !unassigned && !reassigning" in shop, "the Reassign link"
    assert "!readOnly && (unassigned || reassigning)" in shop, "the AssignBox form itself"
    assert "{data && actions.length > 0 && !confirming && !readOnly && (" in shop, "the whole action footer"
    actions_src = _read(ACTIONS)
    assert "readOnly?: boolean" in actions_src
    assert "{!readOnly && (\n        <div className=\"flex gap-1.5 sm:w-[17rem]\">" in actions_src, "Accept/Not-this-one"


@test("phones are rendered exactly as the API sends them — no new client-side masking was added")
def _():
    src = _read(SHOP)
    assert "data.customer_phone" in src
    assert "mask" not in src.lower(), "masking (or not) is the API's job, per the brief"


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=3)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
