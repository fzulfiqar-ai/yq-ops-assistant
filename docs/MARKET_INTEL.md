# Market Intelligence v1 (release R7b)

Product Finds and Field Notes merged into one capture (plan §18). Code: `app/market_intel.py` (logic),
`app/market_intel_api.py` (routes), `web/src/components/MarketCapture.tsx` (the rep's Spotted button),
`web/src/pages/MarketIntel.tsx` (the board and My signals), `web/src/lib/marketIntel.ts` (shapes, resize, retry
queue). Schema: `scripts/r7b_market_intel_migration.sql` (see `docs/MIGRATIONS.md`). Tests:
`python -m tests.test_r7b_market_intel`.

## Who does what

| | capture | own sightings | board / item | review queue | status + edit | approve an action |
|---|---|---|---|---|---|---|
| salesman (with the page) | yes | yes | aggregated, no other rep's name, shop or note | no | no | no |
| admin, member, operations*, sales manager* (with the page) | yes | yes | everything | yes | yes | yes |
| management (page granted by an admin) | no (read-only) | — | everything | read | no | yes, Opportunity only |

\* reserved roles, active once someone holds them. The page is **Market Intel**: a salesman default for new invites,
grantable to management, never tied to "AI Assistant" (granting it does not open /ask). Existing reps get it from
`scripts/r7b_market_intel_grants.sql` or the Team page. Management's approve route is the one write on the central
read-only allowlist (`app/auth.READ_ONLY_WRITE_ALLOWLIST`).

## The capture (target 15–30 s)

Spotted (floating, on Today, the Catalog, Customers and Market Intel) opens the camera in the same tap. 1–4 photos are
resized on the phone (1600 px, WebP q0.75). Then an optional one-line note, a kind chip (New product · Competitor price
· Promotion · Shop asked for · Complaint · Other) and only the quick fields that kind needs. The shop is the shop pill
the staff catalog keeps (`sessionStorage['yq-staff-customer']`); its phone is used once to find the merchant record and
is never stored on the sighting. Salesman, time and shop attach automatically. No AI runs, no photo leaves Supabase.

Server side (`capture`): the extension and magic bytes are checked (`app.uploads.content_matches`), Pillow decodes,
turns the photo upright, cuts it to 1600 px and re-encodes it from pixels only (no EXIF, so no GPS), takes a 64-bit
dHash, and stores it in the PRIVATE `finds` bucket under `market/YYYY/MM/`. Reads hand out 1-hour signed URLs.

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

Idempotency: the phone makes a UUID per capture; the server stores it UNIQUE. A retry returns the first card and
writes nothing. With no signal the capture waits in the page's queue and is retried every 20 s and when the phone is
back online, while the app stays open (true offline needs a service worker: later).

## Statuses and decisions

New → Researching → Opportunity → Approved (source it · price response · promotion · ignore) | Rejected | Merged. A
decision can be reopened (Approved → Opportunity, Rejected → New); Merged is final and moves the sightings to the target.
Every move, edit, identify, merge, approval, AI verification and photo flag is a row in `market_item_decisions`
(append-only): who, role, why.

Photos with people: a reviewer flags them; they stay (blurred) for the office and never reach a rep, a report or the
agent view.

## Demand signals (source = system)

`market_intel.roll_demand_signals(days=7)` turns zero-result marketplace searches (typing prefixes folded: "san" →
"sandisk") and "tell me when back" asks (per SKU, distinct merchants, no phone copied) into sightings, one per term /
SKU per ISO week (uuid5 keys: a re-run writes nothing). It is **not scheduled yet**: call it from
`app/shop_jobs.py` weekly when the owner is ready.

## The weekly AI Head (plan §18.9)

`python -m scripts.market_intel_ai_head pack [--photos]` writes `exports/ai_head/<date>/market_intel.json` from
`v_market_signals_agent` (read-only), ranked by distinct shops × recency; `--photos` adds 1-hour signed links for the
top items (never a people-flagged photo). Claude Code reads them on the owner's PC and writes its readings with
`python -m scripts.market_intel_ai_head suggest FILE.json [--apply]`: stored as `ai_suggestion` + `ai_confidence`,
**Unverified** until a reviewer presses "Mark verified"; an item a person verified is never overwritten; no status or
merge is ever set by it.

## The old boards

`/finds` and `/field-notes` still open (hidden from the nav). `scripts/market_intel_import.py` copies the 259 finds + 2
notes in as `source='import'` (dry run by default): one sighting per find / note, photos referenced where they are,
item-less (the "Finds library" tab), stable ids so a re-run writes nothing. The public `/f/{token}` finds board is
retired (plan §18.8): the link answers "no longer valid".
