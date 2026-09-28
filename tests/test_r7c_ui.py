"""Sprint 3 "Order heart" (R7c), part S3b — the order desk and the rep's field sheet.

    python -m tests.test_r7c_ui

The owner's flow (27-Sep-2026), "as simple as possible; most of the time the shop will not press
OK, so the salesman must be able to":

    Received  →  Confirmed  →  Delivered        (or Cancelled)

Same lightweight runner as tests/test_r7a_b4.py (no pytest, no network, no .env). The page is
TypeScript, so most checks read the sources (ShopOrders.tsx, shop-ops/OrderHeart.tsx,
shop-ops/heart.ts, shop-ops/OrderActions.tsx, shop-ops/pipeline.ts). The pure rules in heart.ts —
which change needs a reason, when the "Shop agreed" tick is required, the confirmed-else-original
money — run for real in plain node through web/scripts/order_heart_ui_test.mjs; that half SKIPS
(prints, never fails) when node or web/node_modules is not there, and ci.yml runs it in the web job.

The API contract the page is coded to is app/shop_heart.py + app/shop_api.py (stream S3a). When
that module is present (after the streams are merged) the last checks compare the page's copies of
the server's exact messages and vocabularies with the server's own; before that they SKIP.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
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


SHOP = "web/src/pages/ShopOrders.tsx"
HEART_UI = "web/src/pages/shop-ops/OrderHeart.tsx"
HEART = "web/src/pages/shop-ops/heart.ts"
ACTIONS = "web/src/pages/shop-ops/OrderActions.tsx"
PIPELINE = "web/src/pages/shop-ops/pipeline.ts"
NODE_TEST = "web/scripts/order_heart_ui_test.mjs"
REP_NODE_TEST = "web/scripts/rep_ui_test.mjs"


def _fn(src: str, head: str) -> str:
    """The body of a top-level function / component, from its declaration to the next one."""
    assert head in src, head
    return src.split(head, 1)[1].split("\nfunction ", 1)[0].split("\nexport function ", 1)[0]


# ── 1. four visible stages, one forward button, no stamps ─────────────────────

@test("heart.ts: packed / out_for_delivery read 'Confirmed'; the four stages and their filters")
def _():
    src = _read(HEART)
    assert "packed: 'confirmed', out_for_delivery: 'confirmed'" in src
    labels = src.split("export const STATUS_LABEL: Record<string, string> = {", 1)[1].split("}", 1)[0]
    assert "packed: 'Confirmed'" in labels and "out_for_delivery: 'Confirmed'" in labels
    assert "Preparing" not in labels and "On the way" not in labels
    assert "export const VISIBLE_STATUSES: readonly VisibleStatus[] = ['new', 'confirmed', 'delivered', 'cancelled']" in src
    assert "confirmed: 'confirmed,packed,out_for_delivery'," in src, "Confirmed also lists the stamped orders"
    # the stamp words survive only as a quiet desk note
    assert "export const STAMP_LABEL: Record<string, string> = { packed: 'Preparing', out_for_delivery: 'On the way' }" in src


@test("desk chips: Needs action · All · Received · Confirmed · Delivered · Cancelled, filtered through FILTER_PARAM")
def _():
    src = _read(SHOP)
    desk = _fn(src, "function DeskOrders(")
    assert "Needs action" in desk
    assert "(['all', ...VISIBLE_STATUSES] as const).map((s) => {" in desk
    assert "visibleCount(counts, s)" in desk and "`${VISIBLE_LABEL[s]} ${n}`" in desk
    assert "type StatusFilter = 'all' | 'unassigned' | 'needs_action' | VisibleStatus" in src
    root = src.split("export default function ShopOrders()", 1)[1]
    assert ": FILTER_PARAM[status])" in root and ": FILTER_PARAM[bucket]" in root
    assert "const STATUSES = [" not in src, "the six-status chip list is gone"


@test("rep's phone tabs: the same four stages (44 px), old ?bucket=new|progress|done deep links still land")
def _():
    src = _read(SHOP)
    field = _fn(src, "function FieldOrders(")
    assert "VISIBLE_STATUSES.map((v) => {" in field and "VISIBLE_LABEL[v]" in field
    assert "min-h-11" in field and "grid-cols-4" in field
    assert "BUCKETS" not in src and "In progress" not in src
    assert "const initialBucket = bucketFromParam(sp.get('bucket'))" in src
    heart = _read(HEART)
    fn = heart.split("export function bucketFromParam", 1)[1].split("\n}", 1)[0]
    assert "s === 'progress'" in fn and "return 'confirmed'" in fn and "s === 'done'" in fn and "return 'delivered'" in fn


@test("no Preparing / On the way buttons anywhere a rep or the desk can tap")
def _():
    for rel in (SHOP, HEART_UI):
        src = _read(rel)
        for gone in ("Mark preparing", "setStatus('packed')", "setStatus('out_for_delivery')", "'On the way'",
                     "status: 'packed'", "status: 'out_for_delivery'", "ACTION_LABEL", "function nextStatuses("):
            assert gone not in src, f"{rel}: {gone}"
    acts = _read(ACTIONS)
    assert "export const ACTION_LABEL" not in acts
    assert "export function ConfirmEditor(" not in acts, "one editor now: OrderHeart.OrderEditor"


@test("one forward button per stage: Received -> 'Confirm order', Confirmed -> 'Delivered', Cancel secondary (drawer + sheet)")
def _():
    src = _read(SHOP)
    for head in ("function OrderDrawer(", "function FieldOrderSheet("):
        body = _fn(src, head)
        assert "can('confirm') && (" in body and "Confirm order" in body, head
        assert "can('deliver') && (" in body and "Delivered" in body, head
        assert "can('cancel') && (" in body, head
        assert body.index("can('confirm') && (") < body.index("can('cancel') && ("), head
        assert "flow.deliverNow" in body, f"{head}: Delivered is one tap"
        assert "flow.setMode('deliver')" in body and "Deliver with changes" in body, head
        assert "flow.setMode('amend')" in body and "Amend" in body, head
    # the buttons come from the server's `actions` (heart.actionsOf), never from next_statuses
    flow = _fn(src, "function useOrderFlow(")
    assert "const { list: actions, legacy } = actionsOf(data, readOnly)" in flow
    assert "next_statuses" not in _fn(src, "function OrderDrawer(") and "next_statuses" not in _fn(src, "function FieldOrderSheet(")


# ── 2. the lines: Requested · Confirmed · Delivered, chips, effective money ────

@test("drawer lines table: Requested · Confirmed · Delivered (only once delivered) · Total, with the line chips")
def _():
    src = _read(SHOP)
    table = _fn(src, "function LinesTable(")
    assert ">Requested</th>" in table and ">Confirmed</th>" in table
    assert "{delivered && <th className=\"px-1.5 py-1.5 text-right\">Delivered</th>}" in table
    assert "<LineChips line={l} showNote />" in table
    assert "<EffectiveTotal now={m.now} was={m.was} />" in table
    phone = _fn(src, "function FieldLines(")
    assert "`Requested ${l.qty}`" in phone and "`Confirmed ${qc}`" in phone and "`Delivered ${" in phone
    assert "<LineChips line={l} showNote />" in phone
    # no confirmed number while the order is still Received
    shown = _fn(src, "function shownConfirmed(")
    assert "vis === 'new' || vis === 'cancelled'" in shown


@test("line chips: reduced / unavailable / substituted -> code / added / comes later, with the PUBLIC reason label")
def _():
    heart = _read(HEART)
    chip = heart.split("export function dispositionChip", 1)[1].split("\n}", 1)[0]
    for word in ("'Reduced'", "'Unavailable'", "`Substituted → ${l.substitute_item_code}`", "'Added'", "'Comes later'"):
        assert word in chip, word
    ui = _read(HEART_UI)
    chips = _fn(ui, "export function LineChips(")
    assert "line.reason_label" in chips, "the public label (reason_label), never the reason code"
    assert "Office note — the shop never sees it" in chips


@test("every total is confirmed-else-original with the as-ordered total struck through: list rows, card, drawer, sheet")
def _():
    src = _read(SHOP)
    # desk table (sorts / exports the same money it shows)
    assert "{ key: 'total_effective_bhd', label: 'Total', align: 'right', render: (_, r) => <EffectiveTotal now={effectiveTotal(r)} was={totalChanged(r) ? r.total_bhd ?? 0 : null} /> }" in src
    assert "total_effective_bhd: effectiveTotal(r)" in src
    # the rep's card
    card = _fn(src, "function OrderCard(")
    assert "<EffectiveTotal now={effectiveTotal(row)} was={totalChanged(row) ? row.total_bhd ?? 0 : null}" in card
    assert "bhd(row.total_bhd, 3)" not in card
    # drawer + sheet share MoneyBlock
    assert src.count("<MoneyBlock o={data}") == 2
    money = _fn(src, "function orderMoney(")
    assert "const now = effectiveTotal(o)" in money and "fils(sub) + fils(delivery) - fils(now)" in money
    heart = _read(HEART)
    assert "return Number(o.total_confirmed_bhd ?? o.total_bhd ?? 0)" in heart
    ui = _fn(_read(HEART_UI), "export function EffectiveTotal(")
    assert "<s " in ui and "As ordered" in ui


# ── 3. one editor for Confirm and Amend ────────────────────────────────────────

@test("OrderEditor serves Confirm and Amend in both views, posting to /confirm or /amend with the contract's body")
def _():
    src = _read(SHOP)
    assert src.count("<OrderEditor") == 2 and src.count("mode={mode}") == 2
    ui = _read(HEART_UI)
    ed = _fn(ui, "export function OrderEditor(")
    assert "`/shop/orders/${order.id}/${mode === 'confirm' ? 'confirm' : 'amend'}`" in ed
    for key in ("body.added_lines = plan.added_lines", "body.expected_delivery = eta.trim()", "body.note = note.trim()",
                "body.shop_agreed = { via: agreed.via }", "{ lines: plan.lines, ...versionOf(order) }"):
        assert key in ed, key
    heart = _read(HEART)
    plan = heart.split("export function planEdit(", 1)[1].split("\n}\n", 1)[0]
    assert "substitute_item_code: d.sub.code, substitute_qty: d.sub.qty" in plan
    assert "line_status: legacy ? 'removed' : 'unavailable'" in plan
    assert "line_status: 'backorder', backorder: true" in plan
    assert "{ item_code: a.code, qty: a.qty, reason: 'customer_changed' }" in plan


@test("editor: live stock per line (In stock N / Only N / Sold out) and a one-tap 'set to available'")
def _():
    heart = _read(HEART)
    sv = heart.split("export function stockView", 1)[1].split("\n}", 1)[0]
    assert "'Sold out'" in sv and "`Only ${q}`" in sv and "`In stock ${q}`" in sv
    ui = _read(HEART_UI)
    row = _fn(ui, "function EditLineRow(")
    assert "Set to {avail} available" in row
    assert "set({ qty: avail, reason: d.reason || 'out_of_stock', backorder: false })" in row, "one tap: the quantity AND the reason"
    assert "Mark unavailable" in row
    pipe = _read(PIPELINE)
    assert "queryKey: ['shop-staff-catalog']" in pipe and "apiGet<CatalogPayload>('/shop/catalog')" in pipe


@test("editor: every changed line needs a reason chip ('other' needs a note); Save stays disabled until then")
def _():
    ui = _read(HEART_UI)
    chips = _fn(ui, "function ReasonChips(")
    assert 'role="radiogroup"' in chips and "value === 'other'" in chips and "aria-required" in chips
    ed = _fn(ui, "export function OrderEditor(")
    assert "plan.needReason.length === 0" in ed
    assert "`Pick a reason for ${plan.needReason.join(', ')}.`" in ed
    heart = _read(HEART)
    assert "return code !== 'other' || note.trim().length >= REASON_NOTE_MIN" in heart
    assert "export const REASON_NOTE_MIN = 3" in heart


@test("editor: substitute picker = same category, in stock, priced, with the price difference; add an item")
def _():
    heart = _read(HEART)
    subs = heart.split("export function substituteOptions(", 1)[1].split("\n}\n", 1)[0]
    assert "it.category !== cat || !inStock(it)" in subs
    assert "if (unit == null) continue" in subs, "an unpriceable item is never offered (the server refuses it)"
    assert "Math.abs(a.diff ?? 0) - Math.abs(b.diff ?? 0)" in subs
    ui = _read(HEART_UI)
    picker = _fn(ui, "function ItemPicker(")
    assert "Same category, in stock — closest price first." in picker
    assert "priceDiff(o.diff)" in picker
    assert "'Same price'" in _fn(ui, "function priceDiff(")
    assert "label=\"Add an item\"" in _fn(ui, "export function OrderEditor(")


@test("the 'Shop agreed' tick: shown only for an adverse change, WhatsApp / Phone / Visit, required to save")
def _():
    ui = _read(HEART_UI)
    box = _fn(ui, "function ShopAgreedBox(")
    assert "Shop agreed" in box and "AGREED_VIA.map((v) =>" in box and 'aria-label="How the shop agreed"' in box
    for head in ("export function OrderEditor(", "export function DeliverEditor("):
        ed = _fn(ui, head)
        assert "const needsTick = plan.adverse.length > 0 || serverAdverse" in ed, head
        assert "const tickOk = !needsTick || (agreed.on && Boolean(agreed.via))" in ed, head
        assert "{needsTick && (" in ed and "<ShopAgreedBox" in ed, head
        assert "if (msg === ADVERSE_MSG) setServerAdverse(true)" in ed, f"{head}: the server's refusal reveals the tick"
    heart = _read(HEART)
    assert "AGREED_VIA: { code: string; label: string }[] = [\n  { code: 'whatsapp', label: 'WhatsApp' },\n  { code: 'phone', label: 'Phone' },\n  { code: 'visit', label: 'Visit' },\n]" in heart


@test("adverse = substitute at another price | backorder | crossing the minimum; a plain stock cut is not")
def _():
    heart = _read(HEART)
    plan = heart.split("export function planEdit(", 1)[1].split("\n}\n", 1)[0]
    assert "kind: 'substitute_price'" in plan and "kind: 'backorder'" in plan
    assert "if (belowMinimum(order, beforeF, afterF)) adverse.push({ kind: 'below_minimum' })" in plan
    below = heart.split("function belowMinimum(", 1)[1].split("\n}", 1)[0]
    assert "minF > 0 && beforeF - dF >= minF && minF > afterF - dF" in below, "an order already under the minimum is not adverse"


# ── 4. Delivered is one tap; Deliver with changes ──────────────────────────────

@test("Delivered = one tap (POST …/deliver with an empty body; an older API: the status route)")
def _():
    src = _read(SHOP)
    flow = _fn(src, "function useOrderFlow(")
    deliver = flow.split("async function deliverNow()", 1)[1].split("\n  }\n", 1)[0]
    assert "apiPost<HeartResponse>(`/shop/orders/${id}/deliver`, {})" in deliver
    assert "apiPost<HeartResponse>(`/shop/orders/${id}/status`, { status: 'delivered' })" in deliver
    assert "legacy" in deliver


@test("Deliver with changes: delivered qty per line + reason + items added at the shop, to …/deliver")
def _():
    ui = _read(HEART_UI)
    ed = _fn(ui, "export function DeliverEditor(")
    assert "`/shop/orders/${order.id}/deliver`" in ed
    assert "body.lines = plan.lines" in ed and "body.added = plan.added" in ed
    assert "Handed over" in ed and "Add an item handed over at the shop" in ed
    assert "Nothing was handed over — cancel the order instead." in ed
    heart = _read(HEART)
    pd = heart.split("export function planDeliver(", 1)[1].split("\n}\n", 1)[0]
    assert "lines.push({ line_id: l.id, qty_delivered: q" in pd
    assert "const beforeF = fils(order.total_confirmed_bhd ?? order.total_bhd)" in pd


# ── 5. Reopen (admin, 7 days, reason) ─────────────────────────────────────────

@test("Reopen: offered only when the server lists it (admins, within 7 days), reason of 3+ chars, POST …/reopen")
def _():
    src = _read(SHOP)
    for head in ("function OrderDrawer(", "function FieldOrderSheet("):
        body = _fn(src, head)
        assert "can('reopen') && (" in body and "flow.setMode('reopen')" in body, head
        assert "<ReopenBox orderId={id} status={data.status} until={data.reopen_until}" in body, head
    ui = _read(HEART_UI)
    box = _fn(ui, "export function ReopenBox(")
    assert "apiPost(`/shop/orders/${orderId}/reopen`, { reason: reason.trim() })" in box
    assert "reason.trim().length >= 3" in box
    heart = _read(HEART)
    assert "return status === 'delivered' ? 'confirmed' : status === 'cancelled' ? 'new' : null" in heart


# ── 6. Tell the shop ──────────────────────────────────────────────────────────

@test("after confirm / amend / deliver the primary action is 'Tell the shop on WhatsApp'; the tap is logged")
def _():
    ui = _read(HEART_UI)
    btn = _fn(ui, "export function TellShopButton(")
    assert "'Tell the shop on WhatsApp'" in btn
    assert "apiPost(`/shop/orders/${orderId}/customer-notified`, { channel })" in btn
    assert "onClick={() => log('whatsapp')}" in btn
    assert "if (legacy) return" in btn, "an API without the route: WhatsApp opens, nothing is logged"
    src = _read(SHOP)
    flow = _fn(src, "function useOrderFlow(")
    assert "setStep({ url: res?.whatsapp_url || res?.order?.whatsapp_url || null })" in flow, "the step's own message (amended text)"
    assert "const tellFirst = Boolean(tellUrl) && can('tell_shop') && (step != null || (data ? shopNotTold(data) : false))" in flow
    for head in ("function OrderDrawer(", "function FieldOrderSheet("):
        body = _fn(src, head)
        assert '<TellShopButton orderId={id} url={flow.tellUrl} variant="primary"' in body, head
        # after a step the forward button steps back to an outline
        assert "flow.tellFirst ?" in body, head


@test("'Shop not told yet' chip until a tap is logged after the latest step (never on a Received order)")
def _():
    src = _read(SHOP)
    assert "Shop not told yet" in src
    assert src.count("<NotToldChip o={data} />") == 2
    heart = _read(HEART)
    fn = heart.split("export function shopNotTold(", 1)[1].split("\n}\n", 1)[0]
    assert "if (visibleStatus(o.status) === 'new') return false" in fn
    assert "return own == null ? o.shop_told === false : !own" in fn, "the events decide; the server flag when they say nothing"


@test("review F3-6: a rep-placed order (born Confirmed in the shop) is already told — no chip, 'Tell the shop' is not the first nag")
def _():
    heart = _read(HEART)
    told = heart.split("export function shopToldFrom(", 1)[1].split("\n}\n", 1)[0]
    assert "ev === 'customer_notified' || (ev === 'status:confirmed' && e?.detail?.born_confirmed === true)) told = true" in told
    assert "ev.startsWith('status:') || ev === 'amended' || ev === 'created' || ev === 'reopened') told = false" in told, \
        "the same steps as shop_heart.notify_state"
    assert "else if (isStampEvent(ev)) continue" in told, "a pick-list stamp changes nothing the shop sees"
    src = _read(SHOP)
    # the chip and the flow read the order WITH its events (the detail payload)
    assert "function NotToldChip({ o }: { o: Parameters<typeof shopNotTold>[0] })" in src
    flow = _fn(src, "function useOrderFlow(")
    assert "(step != null || (data ? shopNotTold(data) : false))" in flow
    assert "events: OrderEvent[]" in src.split("interface OrderDetail extends ShopOrderRow {", 1)[1].split("\n}", 1)[0]
    # the server's own marker for such an order (app/shop.py place_order), when this tree has it
    shop_py = ROOT / "app" / "shop.py"
    if shop_py.exists():
        assert '"born_confirmed": True' in shop_py.read_text(encoding="utf-8")


@test("review F3-6: the rep's field sheet reads three stages only — no pick-list stamp words in its timeline")
def _():
    src = _read(SHOP)
    sheet = _fn(src, "function FieldOrderSheet(")
    assert "collapseReminders(data.events).map((e, i) => (isStampEvent(e.event) ? null : (" in sheet
    assert "STAMP_LABEL" not in sheet and "Pick list" not in sheet
    # StatusPill / StageTrack / the four tabs all go through heart's three visible stages
    assert "<StatusPill status={data.status} />" in sheet and "<StageTrack order={data} />" in sheet
    heart = _read(HEART)
    assert "return event === 'status:packed' || event === 'status:out_for_delivery'" in heart
    # the desk keeps the quiet stamp note (the office runs the pick list)
    assert "`Pick list: ${STAMP_LABEL[event.slice(7)]}`" in _fn(src, "function eventLabel(")


@test("review F3-1: Confirm / Amend / Deliver with changes send expected_updated_at = the payload's updated_at")
def _():
    ui = _read(HEART_UI)
    order = ui.split("export interface HeartOrder extends EditOrder {", 1)[1].split("\n}", 1)[0]
    assert "updated_at?: string | null" in order
    ver = ui.split("function versionOf(order: HeartOrder)", 1)[1].split("\n}", 1)[0]
    assert "return order.updated_at ? { expected_updated_at: order.updated_at } : {}" in ver
    ed = _fn(ui, "export function OrderEditor(")
    assert "const body: Record<string, unknown> = { lines: plan.lines, ...versionOf(order) }" in ed
    dv = _fn(ui, "export function DeliverEditor(")
    assert "const body: Record<string, unknown> = { ...versionOf(order) }" in dv
    # a newer version answers 409 → the existing refresh (onConflict), never a toast of the raw error
    for body in (ed, dv):
        assert "if (e instanceof ApiError && e.status === 409) {\n        onConflict()" in body
    # the editors remount on a fresh payload, so the version sent is always the one on screen
    src = _read(SHOP)
    assert src.count("key={`${mode}-${data.updated_at || ''}`}") == 2
    assert src.count("key={`deliver-${data.updated_at || ''}`}") == 2


# ── 7. management reads everything, taps nothing; phones get 44 px ────────────

@test("management (isReadOnly): no footer, no editor, no WhatsApp tap — heart.actionsOf returns nothing")
def _():
    src = _read(SHOP)
    drawer = _fn(src, "function OrderDrawer(")
    assert "const flow = useOrderFlow(id, onChanged, Boolean(readOnly))" in drawer
    assert "const hasFooter = Boolean(data) && !readOnly && !editing && (flow.actions.some((a) => a !== 'tell_shop') || flow.tellFirst)" in drawer
    assert "data.whatsapp_url && !readOnly && !flow.tellFirst" in drawer
    heart = _read(HEART)
    fn = heart.split("export function actionsOf(", 1)[1].split("\n}\n", 1)[0]
    assert "if (readOnly) return { list: [], legacy }" in fn


@test("44 px targets on phones: chips, small buttons, inputs, the drawer's buttons")
def _():
    ui = _read(HEART_UI)
    assert "const CHIP = 'h-11 rounded-full" in ui and "sm:h-9" in ui.split("const CHIP = ", 1)[1].split("\n", 1)[0]
    assert "const SMALL_BTN = 'inline-flex h-11" in ui
    assert "const INPUT = 'h-11 w-full" in ui
    assert 'size="lg"' in _fn(ui, "function EditLineRow("), "the quantity stepper's buttons are 44 px"
    drawer = _fn(_read(SHOP), "function OrderDrawer(")
    assert drawer.count('className="h-11 flex-1 sm:h-10"') == 2


# ── 8. plumbing that must not drift ────────────────────────────────────────────

@test("409: the CAS message, the editor closes and the order is read again")
def _():
    src = _read(SHOP)
    flow = _fn(src, "function useOrderFlow(")
    conflict = flow.split("function conflict()", 1)[1].split("\n  }\n", 1)[0]
    assert "toast(CAS_MSG, 'error')" in conflict and "setMode('view')" in conflict and "refetch()" in conflict
    ui = _read(HEART_UI)
    for head in ("export function OrderEditor(", "export function DeliverEditor("):
        assert "if (e instanceof ApiError && e.status === 409) {\n        onConflict()" in _fn(ui, head), head


@test("an API from before R7c still works: the old confirm words, no substitute / add / backorder, no amend / reopen")
def _():
    ui = _read(HEART_UI)
    ed = _fn(ui, "export function OrderEditor(")
    assert "const extend = !legacy" in ed and "{extend && (" in ed
    heart = _read(HEART)
    fn = heart.split("export function actionsOf(", 1)[1].split("\n}\n", 1)[0]
    assert "const legacy = !Array.isArray(o.actions)" in fn
    assert "vis === 'new' ? ['confirm', 'cancel'] : vis === 'confirmed' ? ['deliver', 'cancel'] : []" in fn
    src = _read(SHOP)
    # Deliver with changes needs the new route (desk drawer, then the phone sheet)
    assert "{!flow.legacy && (" in _fn(src, "function OrderDrawer(")
    assert "{can('deliver') && !flow.legacy && (" in _fn(src, "function FieldOrderSheet(")


@test("labels for PickList / Today come from heart.ts (Today's rep list reads Confirmed); PickList keeps the API's own label")
def _():
    acts = _read(ACTIONS)
    assert "export { STATUS_LABEL, STATUS_TONE } from './heart'" in acts
    pick = _read("web/src/pages/PickList.tsx")
    assert "{o.status_label || STATUS_LABEL[o.status] || o.status}" in pick, "the storekeeper's stamp words come from the API"


@test("cancel reasons include 'Below the minimum' (shop_pipeline.CANCEL_REASONS below_minimum)")
def _():
    pipe = _read(PIPELINE)
    block = pipe.split("export const CANCEL_REASONS", 1)[1].split("= [", 1)[1].split("\n]", 1)[0]
    assert "{ code: 'below_minimum', label: 'Below the minimum' }" in block
    assert block.index("below_minimum") < block.index("'other'"), "Other stays last"


@test("returns are capped at what was handed over (qty_delivered, else confirmed; 0 for a line that was out)")
def _():
    acts = _read(ACTIONS)
    box = _fn(acts, "export function ReturnBox(")
    assert "l.qty_delivered != null ? l.qty_delivered" in box
    assert "['removed', 'unavailable', 'substituted'].includes(l.line_status || 'ok') ? 0 : l.qty" in box
    assert "const live = lines.filter((l) => cap(l) > 0)" in box


@test("the timeline speaks the new events: amended, reopened, shop told, born confirmed, shop agreed, pick-list stamps")
def _():
    src = _read(SHOP)
    fn = _fn(src, "function eventLabel(")
    for needle in ("event === 'amended'", "event === 'reopened'", "event === 'customer_notified'",
                   "'Placed and confirmed by the rep'", "`Pick list: ${STAMP_LABEL[event.slice(7)]}`",
                   "' with changes'", "agreedText(detail)"):
        assert needle in fn, needle
    assert "` · shop agreed${VIA_WORD[via] ? ` ${VIA_WORD[via]}` : ''}`" in src


@test("the StaffOrder type carries the R7c fields the page reads (all optional: an older API still renders)")
def _():
    src = _read(SHOP)
    detail = src.split("interface OrderDetail extends ShopOrderRow {", 1)[1].split("\n}", 1)[0]
    for field in ("actions?: string[] | null", "shop_told?: boolean | null", "reopen_until?: string | null",
                  "change_reasons?: Record<string, string> | null", "min_order_bhd?: number | null",
                  "steps?: StageStep[] | null", "stamp_label?: string | null", "subtotal_confirmed_bhd?: number | null",
                  "lines: HeartLine[]"):
        assert field in detail, field


@test("ci.yml runs this suite (api job) and the node halves after npm ci (web job)")
def _():
    ci = _read(".github/workflows/ci.yml")
    assert "python -m tests.test_r7c_ui" in ci
    assert "run: node scripts/order_heart_ui_test.mjs" in ci
    assert ci.index("run: npm ci") < ci.index("run: node scripts/order_heart_ui_test.mjs")
    assert "run: node scripts/rep_ui_test.mjs" in ci
    assert ci.index("run: npm ci") < ci.index("run: node scripts/rep_ui_test.mjs")


# ── 9. the pure rules, in plain node ───────────────────────────────────────────

@test("node: heart.ts on hand-worked orders — reasons, the adverse tick, pro-rata money, deliver, actions (web/scripts/order_heart_ui_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / NODE_TEST
    assert script.exists(), NODE_TEST
    if not node or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (ci.yml runs it in the web job)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-4:])
    print("        " + tail.replace("\n", "\n        "))
    assert r.returncode == 0, "node order-heart tests failed:\n" + out[-2000:]


@test("node: the rep's screens — quick-add, the checkout id, the Change carry, shop told, price drops (web/scripts/rep_ui_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / REP_NODE_TEST
    assert script.exists(), REP_NODE_TEST
    if not node or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (ci.yml runs it in the web job)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-4:])
    print("        " + tail.replace("\n", "\n        "))
    assert r.returncode == 0, "node rep-screen tests failed:\n" + out[-2000:]


# ── 9b. the rep's catalog and the floating Spotted button (review stream F3) ──

CAPTURE = "web/src/components/MarketCapture.tsx"
STAFF = "web/src/pages/shop/StaffCatalog.tsx"
QUICK = "web/src/pages/shop/quickAdd.ts"


@test("review F3-2: from 1280 px Spotted stops left of the docked order slip (Total / Place order); the +84 px only above a showing order bar")
def _():
    cap = _read(CAPTURE)
    assert "pathname.startsWith('/shop') ? 84 : 14" not in cap, "no fixed /shop offset: the page says what to clear"
    pos = cap.split("const CAPTURE_POSITION: CSSProperties = {", 1)[1].split("\n}", 1)[0]
    assert "bottom: 'calc(var(--yq-tabbar, 88px) + env(safe-area-inset-bottom, 0px) + var(--yq-dock-bottom, 0px) + 14px)'" in pos
    assert "right: 'max(var(--yq-dock-right, 0px), var(--yq-capture-edge, 16px))'" in pos
    assert "style={CAPTURE_POSITION}" in cap
    assert "[--yq-capture-edge:16px] lg:[--yq-capture-edge:32px]" in cap, "16 px on a phone, 32 px from lg (was right-4 lg:right-8)"
    assert "right-4" not in cap.split("style={CAPTURE_POSITION}", 1)[0].rsplit("<button", 1)[1]
    staff = _read(STAFF)
    dock = staff.split("function useDockRoom(", 1)[1].split("\n}\n", 1)[0]
    assert "root.style.setProperty('--yq-dock-bottom', `${bar ? bar.offsetHeight : 0}px`)" in dock, "the bar's real height, 0 without it"
    assert "root.style.setProperty('--yq-dock-right', slip && xl.matches ? SLIP_ROOM : '0px')" in dock
    assert "new ResizeObserver(apply)" in dock and "root.style.removeProperty('--yq-dock-bottom')" in dock
    assert "<div ref={setOrderBar} className=\"fixed right-0 z-30" in staff and "xl:hidden\"" in staff.split("ref={setOrderBar}", 1)[1][:200]
    assert "useDockRoom(orderBar, !order && !(catalogQ.isError && !data))" in staff
    # SLIP_ROOM is the layout's own numbers: 1600 max, 24 px sides, a 360 px slip, a 24 px gap
    assert "const SLIP_ROOM = 'calc(max(0px, (100vw - var(--yq-sidebar, 0px) - 1600px) / 2) + 408px)'" in staff
    assert "max-w-[1600px] px-4 lg:px-6 xl:grid xl:grid-cols-[minmax(0,1fr)_360px] xl:gap-6" in staff
    assert 24 + 360 + 24 == 408


@test("review F3-2: on /today, Customers and Market Intel the page can scroll its last line out from under Spotted")
def _():
    cap = _read(CAPTURE)
    assert "const SHOW_ON = ['/today', '/shop', '/customers', '/market-intel']" in cap
    eff = cap.split("// while the button floats", 1)[1].split("}, [visible])", 1)[0]
    assert "if (!visible) return" in eff and "root.style.setProperty('--yq-capture-room', CAPTURE_ROOM)" in eff
    assert "root.style.removeProperty('--yq-capture-room')" in eff
    assert "const CAPTURE_ROOM = '66px'" in cap, "52 px button + 14 px"
    # the hook runs before the early return (the rules of hooks)
    assert cap.index("// while the button floats") < cap.index("if (!visible && !open) return null")
    shell = _read("web/src/components/SalesmanShell.tsx")
    assert "pb-[calc(var(--yq-tabbar,88px)+env(safe-area-inset-bottom)+var(--yq-capture-room,0px))]" in shell
    assert "md:pb-[calc(2.5rem+var(--yq-capture-room,0px))]" in shell


@test("review F3-5: quick-add only for an explicit list (separator, x / ×, or one 'code qty' whose word IS a code)")
def _():
    q = _read(QUICK)
    fn = q.split("export function isQuickList(", 1)[1].split("\n}", 1)[0]
    assert "text: string, isCode: (word: string) => boolean" in fn, "the caller says what is a code"
    assert "if (SEPARATOR.test(s)) return true" in fn and "if (TIMES_FIRST.test(s) || TIMES_LAST.test(s)) return true" in fn
    assert "if (isCode(s)) return false" in fn
    assert "if (last && isCode(last[1])) return true" in fn and "return Boolean(first && isCode(first[2]))" in fn
    # the old catch-all "word number" (it turned "iphone 15" into a list) is gone
    assert "/\\S\\s+(?:x|×|\\*)?\\s*\\d{1,4}$/i.test(s)" not in q
    staff = _read(STAFF)
    assert "const codeKeys = useMemo(() => new Set(items.map((i) => codeKey(i.item_code))), [items])" in staff
    assert "const quick = isQuickList(q, (word) => codeKeys.has(codeKey(word)))" in staff


# ── 9c. price drops in the rep's app (R7e, sub-stream C — no backend change) ──

DROPS = "web/src/pages/shop/priceDrops.ts"
SHARED = "web/src/pages/shop/shared.ts"
FILTERS = "web/src/pages/shop/staffFilters.ts"
FILTERS_SHEET = "web/src/pages/shop/FiltersSheet.tsx"
CARD = "web/src/pages/shop/ProductCard.tsx"
ROW = "web/src/pages/shop/ProductRow.tsx"
SHEET = "web/src/pages/shop/ProductSheet.tsx"
TODAY = "web/src/pages/sales/Today.tsx"
TODAY_DROPS = "web/src/pages/sales/PriceDrops.tsx"


@test("R7e price drops: one rule — the market's priceAnchor (was_bhd above today's price); Was pill, never struck through")
def _():
    src = _read(DROPS)
    assert "import { priceAnchor } from '@/market/lib/format'" in src, "the marketplace's own Was → Now rule"
    assert "const a = priceAnchor(item)" in src and "if (!a || a.kind !== 'was') return null" in src
    assert "return `Was ${money(was)} · ↓${pct}%`" in src, "the market's wasPill wording"
    assert "`${name} — now ${bhd(d.now)} (was ${bhd(d.was)}, ↓${d.pct}%)`" in src
    assert "`${url.origin}/p/${encodeURIComponent(code)}${slug ? `?ref=${encodeURIComponent(slug)}` : ''}`" in src
    for rel in (DROPS, TODAY_DROPS):
        assert "line-through" not in _read(rel) and "<s " not in _read(rel), f"{rel}: the old price is never struck through"
    # every surface asks the same helper
    for rel in (CARD, ROW, SHEET):
        assert "priceDropOf(item)" in _read(rel) and "wasText(drop.was, drop.pct)" in _read(rel), rel
    assert "{drop && <Badge tone=\"rose\">Price drop</Badge>}" in _read(ROW), "the desk's stock cell names it"
    assert "const text = dropShareText(item)" in _read(SHEET), "the shared words carry was → now"


@test("R7e labels: Clearance -> 'Clearing line(s)', On offer -> 'Deal'; the Filters sheet gains 'Price drops'")
def _():
    shared = _read(SHARED)
    assert "on_offer: { label: 'Deal', tone: 'green' }" in shared
    assert "clearance: { label: 'Clearing line', tone: 'amber' }" in shared
    assert "'On offer'" not in shared and "label: 'Clearance'" not in shared
    sheet = _read(FILTERS_SHEET)
    assert "{ key: 'drops', label: 'Price drops', hint: 'Trade price cut in the price book' }" in sheet
    assert "{ key: 'clearance', label: 'Clearing lines'," in sheet and "label: 'Clearance'" not in sheet


@test("R7e chips: the market's order via shared.shownBadges (the BADGE_ORDER indexOf -1 bug is gone); price drop wins")
def _():
    shared = _read(SHARED)
    assert "const BADGE_ORDER: readonly string[] = ['on_offer', 'price_drop', 'clearance', 'best_seller', 'selling_fast', 'new', 'trending']" in shared
    fn = shared.split("export function shownBadges(", 1)[1].split("\n}", 1)[0]
    assert "return n < 0 ? BADGE_ORDER.length : n" in fn, "an unknown badge ranks last, never first"
    assert ".filter((b) => !(drop && b === 'clearance'))" in fn
    card = _read(CARD)
    assert "BADGE_ORDER" not in card and "const badges = shownBadges(item, 2)" in card
    assert "shownBadges(item).map((b) => {" in _read(SHEET)
    flt = _read(FILTERS)
    assert "drops: boolean" in flt and "NO_FILTERS: StaffFilters = { inStock: false, drops: false, clearance: false, best: false }" in flt
    assert "(!f.drops || priceDropOf(i) != null)" in flt and "(!f.clearance || isClearing(i))" in flt
    assert "return hasBadge(i, 'clearance') && !hasBadge(i, 'price_drop')" in flt


@test("R7e catalog: ?f=drops opens on Price drops; the sheet shares the product on the rep's own link")
def _():
    staff = _read(STAFF)
    assert "new URLSearchParams(location.search).get('f') === 'drops' ? { ...NO_FILTERS, drops: true } : NO_FILTERS" in staff
    assert "const passFilters = useCallback((i: ShopItem) => passesStaffFilters(i, filters), [filters])" in staff
    assert "drops: inCat.filter((i) => priceDropOf(i) != null).length," in staff
    assert "Number(filters.drops)" in staff.split("const activeFilters = ", 1)[1].split("\n", 1)[0]
    assert "queryKey: ['shop-today'], queryFn: () => apiGet<TodayData>('/shop/me/today'), enabled: !!sheetItem" in staff
    assert "const shareUrl = sheetItem ? productShareUrl(linkQ.data?.me?.link || '', sheetItem.item_code) : ''" in staff
    assert "canShare={Boolean(shareUrl)}" in staff and "shareUrl={shareUrl}" in staff
    assert "canShare={false}" not in staff, "the staff sheet shares again (with the rep's link)"
    # the hook sits above the early returns (the rules of hooks)
    assert staff.index("const linkQ = useQuery(") < staff.index("if (catalogQ.isError && !data) {")


@test("visual QA: a switched-on filter is a removable chip beside the result count (Price drops is on screen at 390 px)")
def _():
    staff = _read(STAFF)
    flt = _read(FILTERS)
    res = staff.split("{/* ── results ── */}", 1)[1].split("{!data ? (", 1)[0]
    assert "FILTER_CHIPS.filter((c) => filters[c.key]).map((c) => (" in res and "flex-wrap" in res
    assert "onClick={() => setFilters((f) => ({ ...f, [c.key]: false }))}" in res and '<X size={12} aria-hidden="true" />' in res
    # the chips say what the Filters sheet says
    sheet = _read(FILTERS_SHEET)
    for key, label in (("drops", "Price drops"), ("clearance", "Clearing lines"), ("best", "Best sellers"), ("inStock", "In stock")):
        assert f"{{ key: '{key}', label: '{label}' }}" in flt and f"{{ key: '{key}', label: '{label}'," in sheet, key


@test("R7e Today: '{n} products got a lower price' from the catalog's cache, top 5, WhatsApp per row, See all -> /shop?f=drops")
def _():
    today = _read(TODAY)
    assert "import { PriceDrops } from './PriceDrops'" in today
    assert "<PriceDrops link={link} />" in today and today.index("<PriceDrops link={link} />") < today.index("<ComingSoon link={link} />")
    src = _read(TODAY_DROPS)
    assert "useQuery({ queryKey: ['staff-catalog'], queryFn: getStaffCatalog, staleTime: 5 * 60_000" in src, "the Catalog tab's own cache"
    assert "{n} {n === 1 ? 'product' : 'products'} got a lower price" in src
    assert "const TOP = 5" in src and ".slice(0, TOP)" in src
    assert "if ((catalogQ.isError && !catalogQ.data) || !drops.length) return null" in src, "no drops or no catalog: no card"
    assert 'to="/shop?f=drops"' in src and "See all" in src
    assert "href={`https://wa.me/?text=${encodeURIComponent(dropShareText(item, url))}`}" in src
    assert "const url = productShareUrl(link, item.item_code)" in src
    # honest: no urgency words, no counters
    low = src.lower()
    for word in ("hurry", "last chance", "ends in", "only today", "limited time", "don't miss"):
        assert word not in low, word
    # the 390 px phone: the text column can shrink, the row never pushes the card wider
    assert '<span className="min-w-0 flex-1">' in src and "truncate" in src and "overflow-hidden rounded-2xl" in src


# ── 10. the page's copies of the server's words (after the streams are merged) ─

def _server() -> str | None:
    p = ROOT / "app" / "shop_heart.py"
    return p.read_text(encoding="utf-8") if p.exists() else None


@test("contract: ADVERSE_MSG, the reason chips and the agreed-via list match app/shop_heart.py")
def _():
    srv = _server()
    if srv is None:
        print("        SKIP: app/shop_heart.py not in this tree yet (stream S3a) — runs once merged")
        return
    heart = _read(HEART)
    m = re.search(r'^ADVERSE_MSG = "(.+)"$', srv, re.M)
    assert m, "ADVERSE_MSG in shop_heart.py"
    assert f'export const ADVERSE_MSG = "{m.group(1)}"' in heart
    codes = re.findall(r'^    "([a-z_]+)": "[^"]+",$', srv.split("CHANGE_REASONS: dict[str, str] = {", 1)[1].split("}", 1)[0], re.M)
    ui_codes = re.findall(r"\{ code: '([a-z_]+)', label: '[^']+' \}",
                          heart.split("export const CHANGE_REASONS", 1)[1].split("= [", 1)[1].split("\n]", 1)[0])
    assert codes == ui_codes, (codes, ui_codes)
    via = re.search(r'AGREED_VIA: tuple\[str, \.\.\.\] = \(([^)]*)\)', srv)
    assert via and [v.strip().strip('"') for v in via.group(1).split(",") if v.strip()] == ["whatsapp", "phone", "visit"]
    assert "REOPEN_DAYS = 7" in srv


@test("rc review (21): the rep's Today card names only Received / Confirmed / Delivered — never the storekeeper's stamps")
def _():
    src = _read("web/src/pages/sales/Today.tsx")
    low = src.lower()
    for word in ("preparing", "on the way", "out for delivery", "packed", "in progress"):
        assert word not in low, f"Today.tsx names a hidden stage: {word!r}"
    assert "confirmed, not yet delivered" in src
    assert 'to="/shop-orders?bucket=confirmed"' in src and "bucket=progress" not in src


@test("contract: the CAS message and the new routes exist on the API the page calls")
def _():
    srv = _server()
    if srv is None:
        print("        SKIP: app/shop_heart.py not in this tree yet (stream S3a) — runs once merged")
        return
    shop = _read("app/shop.py")
    m = re.search(r'^CAS_CONFLICT_MSG = "(.+)"$', shop, re.M)
    assert m and f"export const CAS_MSG = '{m.group(1)}'" in _read(HEART)
    api = _read("app/shop_api.py")
    for route in ('@app.post("/shop/orders/{order_id}/confirm")', '@app.post("/shop/orders/{order_id}/amend")',
                  '@app.post("/shop/orders/{order_id}/deliver")', '@app.post("/shop/orders/{order_id}/reopen")',
                  '@app.post("/shop/orders/{order_id}/customer-notified")'):
        assert route in api, route
    for field in ('o["actions"]', 'o["reopen_until"]', 'o["change_reasons"]', 'o["min_order_bhd"]', 'notify_state'):
        assert field in api, field
    pipe = _read("app/shop_pipeline.py")
    assert '"below_minimum":' in pipe


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
