# Market Intelligence (v1: release R7b · v2: release R8m)

Product Finds and Field Notes merged into one capture (plan §18), then extended in v2 with a quality track: issue
types, cases with a verification workflow, a safety flag, supplier actions and a monthly supplier workbook (plan Part F).
v2 is described in [§ v2](#v2--quality-capture-review-and-the-supplier-workbook-release-r8m) below; the one-page guide
for reps is `docs/MARKET_INTEL_HOWTO.md`.

Code: `app/market_intel.py` (logic), `app/market_intel_api.py` (routes), `web/src/components/MarketCapture.tsx` (the
Spotted button), `web/src/pages/MarketIntel.tsx` (the board, My signals / My reports), `web/src/lib/marketIntel.ts`
(shapes, resize, retry queue). v2 adds `app/market_quality.py` + `app/market_quality_api.py` (lists, cases, actions) and
`app/supplier_reports.py` + `app/supplier_reports_api.py` (the workbook). Schema: `scripts/r7b_market_intel_migration.sql`
and `scripts/r8m_market_quality_migration.sql` (tables in `docs/SCHEMA.md`, apply steps in `docs/MIGRATIONS.md`). Tests:
`python -m tests.test_r7b_market_intel`, `python -m tests.test_r8m_market_quality_replay`.

## Who does what

| | capture | own sightings | board / item / case | review queue | status + edit | supplier actions | supplier report: build / approve / download | lists |
|---|---|---|---|---|---|---|---|---|
| salesman (with the page) | yes | yes, with status words; answers office questions | aggregated, no other rep's name, shop or note | no | no | no | no | no |
| member, operations*, sales manager* (with the page) | yes | yes | everything | yes | yes | yes | build, approve, download | no |
| admin | yes | yes | everything | yes | yes | yes | build, approve, download | yes |
| management (page granted by an admin) | no (read-only) | — | everything, free text masked | read | no | no | approve, download | no |

\* reserved roles, active once someone holds them. Reviewers = `REVIEW_ROLES` (admin, member, operations, sales
manager); approvers = `APPROVE_ROLES` (reviewers + management). The page is **Market Intel**: a salesman default for new
invites, grantable to management, never tied to "AI Assistant" (granting it does not open /ask). Existing reps get it from
`scripts/r7b_market_intel_grants.sql` or the Team page. Management's approve routes are the only writes on the central
read-only allowlist (`app/auth.READ_ONLY_WRITE_ALLOWLIST`). A storekeeper granted the page counts as office.

## The capture

**Where.** Spotted floats on the salesman app (Today, the Catalog, Customers and Market Intel). In v1 it lived only in
the salesman shell, so office staff could not report from the portal even though the API allowed it; from v2 the same
button is mounted in the office portal too, for every role that holds the page except management (read-only). An office
capture carries no shop unless one is picked.

**On the phone.** The camera opens in the same tap. 1–4 photos are resized on the phone (1600 px, WebP q0.75). v1 then
asks for an optional one-line note, a kind chip (New product · Competitor price · Promotion · Shop asked for · Complaint ·
Other) and only the quick fields that kind needs; v2's sheet is described below. The shop is the shop pill the staff
catalog keeps (`sessionStorage['yq-staff-customer']`); its phone is used once to find the merchant record and is never
stored on the sighting. Salesman, time and shop attach automatically. No AI runs, no photo leaves Supabase.

**Capture time.** v1 stamped `observed_at` with the server's receipt time, so a capture that waited in the retry queue
got its retry time. From v2 the phone sends `client_observed_at` (when the photo was taken): it is kept when it lies
within the last 7 days and at most 10 minutes ahead of the server clock, and `observed_at` then uses it; otherwise the
server time is used. Windows (the 30-day "seen before", the storage month, the weekly keys) are still counted in UTC.

**On the server** (`capture`): the extension and magic bytes are checked (`app.uploads.content_matches`), a photo over
16 MP (read from the header) is refused before anything is decoded, Pillow decodes (a JPEG at a reduced scale, `draft`),
turns the photo upright, cuts it to 1600 px BEFORE any mode conversion and re-encodes it from pixels only (no EXIF, so no
GPS), takes a 64-bit dHash, and stores it in the PRIVATE `finds` bucket under `market/YYYY/MM/`. v2 also writes a 320 px
WebP thumbnail next to it (`<stem>-t320.webp`, `market_photos.thumb_path`) and records the master's sha256; lists and the
board sign thumbnails, the item / case page signs masters. Reads hand out 1-hour signed URLs. Management reads every note,
title, shop name and competitor with phones and emails masked (`app.ai_insights.mask_text`).

The result card, decided in this order (nothing ever merges on fuzzy evidence):

1. **exact barcode** (digits) → that item (a merged item forwards to its target);
2. the typed name / code **is a YQ catalog item** (the whole text is a code, the first word is a code and no other brand
   was named, or it equals a catalog name) → **Already in YQ**: the product card, the sighting on an item keyed by the SKU
   and the kind;
3. the same **kind + brand + category + normalised name** (sorted tokens, prices and stop words out) seen within 30 days
   → **Seen before**: "Added your sighting to X (now 4 shops)";
4. text and no match → **New market find** (a New item);
5. a photo and no text → **Saved: office will identify** (no item until the office identifies it).

A photo within Hamming 8 of a stored one is flagged "near-duplicate" (also catches old WhatsApp photos once the import
ran with `--with-phash`). Counts are always DISTINCT shops and reps.

Idempotency: the phone makes a UUID per capture; the server stores it UNIQUE. A retry returns the first card and writes
nothing. With no signal the capture waits in the page's queue and is retried every 20 s and when the phone is back online,
while the app stays open (true offline needs a service worker: later).

## Statuses and decisions (the opportunity track: `market_items`)

New → Researching → Opportunity → Approved (source it · price response · promotion · ignore) | Rejected | Merged. A
decision can be reopened (Approved → Opportunity, Rejected → New); Merged is final and moves the sightings to the target.
From v2 a new sighting that matches a Rejected item (by barcode) reopens it to Researching, with a decision row ("new
sighting after rejection"). Every move, edit, identify, merge, approval, AI verification and photo flag is a row in
`market_item_decisions` (append-only): who, role, why.

Photos with people: a reviewer flags them. In v1 the blur was CSS only (the signed link still opened the original);
from v2 the server writes a blurred copy (`<stem>-blur.webp`, `market_photos.blurred_path`) and, while the flag is on,
signs only that copy (an admin may ask for the original with `?raw=1`). Flagged photos never reach a rep, a supplier
report or the agent views. Unflagging signs the original again; the original is never overwritten.

## Demand signals (source = system)

`market_intel.roll_demand_signals(days=7)` turns zero-result marketplace searches (typing prefixes folded: "san" →
"sandisk"), "tell {rep}" product requests (`shop_events.event = 'product_request'`, R7d; before that migration the same
tap is stored as a `search_zero` row) and "tell me when back" asks (per SKU, distinct merchants, no phone copied) into
sightings, one per term / SKU per ISO week (uuid5 keys: a re-run writes nothing). v1 left it unscheduled; from v2 it runs
weekly as a job in `app/shop_jobs.JOBS` (Sunday morning, Bahrain time).

## The weekly AI Head (plan §18.9)

`python -m scripts.market_intel_ai_head pack [--photos]` writes `exports/ai_head/<date>/market_intel.json` from
`v_market_signals_agent` (read-only), ranked by distinct shops × recency; `--photos` adds 1-hour signed links for the top
items (never a people-flagged photo). Claude Code reads them on the owner's PC and writes its readings with
`python -m scripts.market_intel_ai_head suggest FILE.json [--apply]`: stored as `ai_suggestion` + `ai_confidence`,
**Unverified** until a reviewer presses "Mark verified"; an item a person verified is never overwritten; no status or
merge is ever set by it. From R8m, `v_market_cases_agent` (cases with codes, counts and dates; no title, note, person,
shop or photo path) is readable by the same read-only role for a later cases section of the pack.

## The old boards

`/finds` and `/field-notes` still open (hidden from the nav). `scripts/market_intel_import.py` copies the 259 finds + 2
notes in as `source='import'` (dry run by default): one sighting per find / note, photos referenced where they are,
item-less (the "Finds library" tab), stable ids so a re-run writes nothing. The public `/f/{token}` finds board is
retired (plan §18.8): the link answers "no longer valid".

---

## v2 — quality capture, review and the supplier workbook (release R8m)

v1 folded every complaint about a SKU into one open "complaint" item, with no issue type, quantity, batch, packaging
version, verification or supplier. v2 keeps the one capture and the one evidence store and adds a **quality track** next
to the opportunity clusters: quality and packaging reports become **cases**; demand, competitor and trend sightings keep
using `market_items` exactly as above. Release scope: Phase 0 (adoption and housekeeping), Phase 1 (quality capture and
review) and Phase 2 (the monthly supplier workbook). Complaint rates, trends and full supplier corrective-action tracking
come later (Phases 3–4).

### What a rep sees (Spotted v2; target ≤ 30 s with a photo only, ≤ 60 s for a complaint)

1. One tap opens the camera.
2. **What is it?** Six large tiles: Quality problem · Packaging · Customer asked for · Competitor · New product/trend ·
   Other.
3. **Which product?** Items this shop bought, recent items, or a search with thumbnails (`GET /market-intel/products`),
   plus **Not sure / not ours**: the rep never invents a SKU; the office identifies it later.
4. **Issue chips** for Quality problem and Packaging, from the admin list of issue types (other tiles keep v1's quick
   fields). A quantity stepper when the issue type asks for it, **Customer said** / **I saw it**, an optional photo of the
   label or batch code (photo role `label_batch`).
5. A note by voice (English or Arabic) or text; the shop is pre-filled; Save.
6. A safety chip (heat, smoke, swelling, shock, or any issue type marked "safety") raises the flag; the rep sees
   "Manager alerted".

The capture sheet has its own English / Arabic strings (right to left in Arabic); the rest of the portal stays English.

**My reports.** Each of the rep's own reports shows its case status in plain words, and an office question can be
answered in place (text and an optional photo), which sends the case back to review. A rep never sees other reps' or
shops' names.

| case status | what the rep reads |
|---|---|
| `submitted` | Received |
| `under_review` | Being looked at |
| `more_info_required` | We need more from you |
| `verified` | Confirmed |
| `not_substantiated` | Not confirmed |
| `action_requested` | Sent to the supplier |
| `resolved` | Fixed |
| `closed` | Closed |

### How a report becomes a case

A report whose issue type is of kind quality or packaging joins an **open** case (not closed, not "not substantiated",
not merged) with the same product (`yq_item_code`) and issue type last seen within 60 days; otherwise it opens a new case
titled with the product name (or the typed text, for "not sure / not ours") and the issue label. A case starts at
evidence level 2 when a photo came with the report, else 1. Every change is a row in `market_case_events`
(append-only): created, status, evidence, question, answer, link, move, merge, edit, action, safety_alert.

### Office workflow (reviewers)

Market Intel gains tabs **Cases** (sub-tabs Safety · New · Need info · Photos to identify, with filters and thumbnails),
**Lists** (admin) and **Supplier reports**, next to Board and Library (the board now pages and shows a true count). The
case page shows the timeline (events, sightings, photos), a verification panel, the evidence level, a question to the rep,
merge / move and the supplier action panel.

Status moves (anything else answers 409):

| from | to |
|---|---|
| submitted | under_review · more_info_required · verified · not_substantiated |
| under_review | more_info_required · verified · not_substantiated |
| more_info_required | under_review · verified · not_substantiated |
| verified | action_requested · closed |
| not_substantiated | closed · under_review (reason required) |
| action_requested | resolved (needs a supplier action with the supplier's response and YQ's resolution check) |
| resolved | closed |
| closed | under_review (admin only, reason required) |

- **Verified** needs a method (sample inspected · photos judged sufficient · several shops) and a reason, and raises the
  evidence level to at least 3. **Not substantiated** needs a reason. **No complaint is treated as a proven defect until a
  person verifies it.**
- A question to the rep sets `more_info_required`; the rep's answer sets `under_review` again.
- Cases merge like items (`merged_into`); a sighting can be moved to another case; counts are refreshed on both.
- A supplier action (request, owner, due date, supplier) is linked to one or more cases and moves them to
  `action_requested`; its response and resolution check are recorded on the action.

**Evidence levels** (a separate field that only ever moves up): 1 Reported · 2 Evidence submitted · 3 Inspected &
verified by YQ · 4 Supplier confirmed · 5 Resolution verified.

The database checks the values (statuses, evidence 1–5, severity 1–3, methods, kinds, photo roles); the transitions,
"only up" and what each status needs are enforced by the API.

### Safety

A safety chip, or an issue type whose safety default is on, sets the case's `safety` flag. The case goes to the Safety
tab and one alert e-mail per case is sent through `app.emailer.send_html` to its default recipients (`ALERT_EMAIL_TO` /
`OWNER_EMAILS`), recorded as a `safety_alert` event; the capture answers `{"safety_alerted": true}`. A failed e-mail never
blocks the capture. A safety case should be reviewed within one business day.

### Lists (admin, no code change to add anything)

- **Suppliers**: name (one active row per name, any case), optional code, active, merge into another.
- **Brands**: name, supplier, aliases; a rename keeps the old name in `aliases` and `renamed_from`; merges.
- **Issue types**: kind (quality, packaging, demand, competitor, other), a stable lowercase code, English and Arabic
  labels, safety default, the extra capture fields (qty, batch_ref, packaging_version, label_photo), active, sort.
- **Categories** come from the existing category master the catalog uses.

Nothing is seeded by the migration (the repository is public): an admin adds the first suppliers, brands and issue types
on the Lists page. A list entry in use is deactivated or merged, never deleted (the database refuses the delete). Each
sighting keeps the brand and supplier as linked or typed at capture time (`brand_text`, `supplier_text`), so history
reads the same after a rename or merge.

### The monthly supplier workbook

A reviewer picks a supplier and a month: the server builds a **draft** from that month's sightings and cases of that
supplier (through the sighting's supplier, its brand's supplier or its case's supplier): counts by issue type, new vs
recurring (recurring = same SKU and issue with ≥ 2 cases or ≥ 3 shops in 90 days), open actions, and a narrative of
template sentences in which every finding cites at least one `[MI-<observation id>]` and every action `[CA-<action id>]`.
Weak evidence (no photo, level 1, a single report) goes to an "Insufficient evidence" paragraph and is not counted.
Reviewers may edit the narrative and the included sightings while it is a draft; an approver approves it, and an earlier
approved report of the same supplier, month and edition becomes superseded. Download (approved only) builds the `.xlsx`
with openpyxl and logs `{at, by, sha256}`.

Sheets of the supplier edition: Summary (period, counts by issue, new vs recurring, narrative with IDs, open actions),
Product issues, Packaging, Evidence (one row per photo with a 640 px JPEG and a short sha256) and Corrective actions. The
internal edition adds Competitor and Demand sheets. Every finding row is labelled "Verified" (level ≥ 3) or "Unverified
report"; evidence levels are shown as words. **Never in a supplier edition:** shop or customer names, rep names ("field
team" instead), YQ prices, costs or margins, photos flagged with people, other suppliers' rows. **Never e-mailed
automatically**: management sends an approved workbook by hand.

### Privacy rules

- A rep sees only his own reports and their status words, never another rep's or shop's name.
- Management reads cases read-only with free text masked (phones and e-mails → `[number]` / `[email]`).
- Photos flagged with people are served blurred only (see above) and never leave YQ.
- Supplier editions: captions and descriptions masked (`app.ai_insights.mask_text`), product / packaging / label photos
  only, none of the exclusions above.
- `v_market_cases_agent` (for the weekly AI Head) carries no title, note, person, shop or photo path.

### Defaults chosen for the plan's open questions (changeable later)

- Suppliers, brands and issue types are admin-managed lists; the workbook works for any supplier.
- Photos in a supplier edition: product, packaging and label photos only; people-flagged photos never.
- Batch and packaging version are optional free text.
- Warranty: not built.
- Retention (policy only, no automatic deletion job in this release): 3 years for verified or actioned cases, 12 months
  for not-substantiated ones, photos with people deleted at closure.
- Verifiers: `REVIEW_ROLES`; supplier-report approvals: `APPROVE_ROLES`.
- Safety alerts: e-mail to the emailer's default recipients plus the Safety tab.
- Storage: 320 px WebP thumbnails now; archiving older masters (local encrypted store or R2) later.

### Data

New tables `suppliers`, `brands`, `market_issue_types`, `market_cases`, `market_case_events` (append-only),
`supplier_actions`, `supplier_action_cases`, `supplier_reports` and the view `v_market_cases_agent`; new columns on
`market_observations` (case and issue links, basis, quantity, batch, packaging version, safety flag, phone capture time,
brand / supplier links and snapshots), `market_photos` (role, sha256, thumbnail, caption, blurred copy) and
`catalog_items` (`barcode`). Column-level detail: `docs/SCHEMA.md` § Market Intelligence. Apply, rehearse and reverse:
`docs/MIGRATIONS.md` § Release R8m.
