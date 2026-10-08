/** Is a page still showing figures from before the newest data load? Pure: no React, no fetch, no import
 *  (web/scripts/numbers_ui_test.mjs exercises it in node).
 *
 *  Two dates say how far the loaded Focus data runs, and both are the last loaded sale day: the header chip's
 *  (GET /freshness, asked again every five minutes on every portal page) and the one a page's own payload
 *  carries (`data_as_of`). After a load the chip moves first. A page whose date is then the earlier one is
 *  showing the old figures under a header that already reads the new date, and should ask again. */

/** '2026-10-06…' → '2026-10-06'; '' for anything that is not a day */
function isoDay(v?: string | null): string {
  const m = /^\d{4}-\d{2}-\d{2}/.exec(v || '')
  return m ? m[0] : ''
}

/** True when the chip's Focus date is later than the date the page's figures carry (a page with no date at all
 *  is behind any chip date). False while the page has nothing yet — its first read is under way — when the
 *  chip has no date, and when the page is level with the chip or ahead of it: only a page that is behind asks. */
export function behindFocus(focusTo: string | null | undefined,
  page: { data_as_of?: string | null } | null | undefined): boolean {
  if (!page) return false
  const chip = isoDay(focusTo)
  return chip !== '' && chip > isoDay(page.data_as_of)
}
