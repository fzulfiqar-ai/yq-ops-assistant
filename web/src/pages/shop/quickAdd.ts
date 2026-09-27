/** Between two lines of a list: a comma, a semicolon or a new line (and the Arabic comma / semicolon). */
const SEPARATOR = /[,;\n،؛]/
/** An explicit "times": a quantity, x / × / *, a space, a word — "24 x C18", "3x C18", "2 × C18"
 *  (the shapes the list parser, market/lib/quickParse.parseToken, reads). */
const TIMES_FIRST = /^\d{1,4}\s*[x×*]\s+\S/i
/** A word, then x (spaced) / × / *, then a quantity — "C18 x 3", "C18 x3", "C18×3". */
const TIMES_LAST = /\S(?:\s+x|\s*[×*])\s*\d{1,4}$/i

/**
 * Does the search field hold an order list ("C18 3, UK15 6") rather than a search? Only when the
 * rep says so, because a search that turns into a list loses its results:
 *
 *   • a separator between lines — ',' ';' or a new line;
 *   • an explicit times sign between a quantity and a word — 'x', '×' or '*' ("24 x C18", "C18 x 3";
 *     a glued "3xC18" is not one: an x inside a code is as likely);
 *   • ONE "code quantity" (or "quantity code") whose word is exactly an item code: "C18 3".
 *
 * Anything else is a search on the whole text: "iphone 15", "tws 4", "20w 2 port" stay searches.
 * `isCode(word)` says whether a word is exactly an item code (the caller's codeKey match); a text
 * that is itself a code (one that ends in a number) is a search that finds it first.
 */
export function isQuickList(text: string, isCode: (word: string) => boolean): boolean {
  const s = text.trim()
  if (!s) return false
  if (SEPARATOR.test(s)) return true
  if (TIMES_FIRST.test(s) || TIMES_LAST.test(s)) return true
  if (isCode(s)) return false
  const last = s.match(/^(.+?)\s+(\d{1,4})$/)
  if (last && isCode(last[1])) return true
  const first = s.match(/^(\d{1,4})\s+(.+)$/)
  return Boolean(first && isCode(first[2]))
}
