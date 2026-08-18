# Email draft — Focus Softnet data access request

Attach: `docs/Focus ERP Data Scope - YQ Bahrain.docx`

---

**Subject:** Data access request — automating our Focus reports

---

Dear [Name],

Thank you for coming back to us about which API we need.

To answer that properly I've attached a short scope document. It sets out exactly what we
need: ten datasets, the fields required for each, and how often. Everything is **read-only** —
we are not asking to create, edit or post anything back into Focus. It stays the system of
record and the only place transactions are entered.

The main thing I'd like to flag is that we are **not tied to an API**. Today someone on our
team logs into Focus every morning, runs eight reports by hand and downloads them. Removing
that manual step is the goal, and there is more than one way to get there. The document sets
out three routes and we are happy with whichever you can properly support:

1. **Scheduled report delivery** — Focus runs the same eight reports on a schedule and
   delivers the files to us (SFTP, a folder, or email). This is our preference, because the
   report definitions don't change and our system already reads these files as they are.
2. **Read-only database access** — a SELECT-only login limited to the sales, stock,
   receivables and pricing tables.
3. **REST API** — GET only, covering the datasets listed in the document.

Could you let us know:

- which of the three you can support on our licence and version, and which you'd recommend
- the cost of each, and whether it is one-time, annual, or per-use
- roughly how long delivery would take

Section 5 of the document lists the specific technical points we need confirmed before we
commit — in particular whether we can request only records that have changed since the last
sync, rather than pulling the full history every time.

Happy to arrange a call if that's easier than replying in writing.

Best regards,

**Furqan Ahmed**
YQ Bahrain Mobile Accessories W.L.L

---

## Notes before sending

- Replace `[Name]` with your Focus account manager.
- Attach the `.docx` (not the HTML).
- If you already know they'll push the API, still leave Route 1 in — it's the cheapest
  outcome for you and gives you a fallback if the API quote comes back high.
- If they answer "the API can't filter by changed-since", that is a genuine reason to pick
  Route 1 or 2 instead. Don't let it be glossed over.
