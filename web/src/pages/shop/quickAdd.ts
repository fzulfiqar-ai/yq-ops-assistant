/** Does the field hold an order list ("C18 3, UK15 6", "24 x C18") rather than a search? */
export function isQuickList(text: string): boolean {
  const s = text.trim()
  if (!s) return false
  if (/[,;\n]/.test(s)) return true
  return /^\d{1,4}\s*(?:x|×|\*)?\s+\S/i.test(s) || /\S\s+(?:x|×|\*)?\s*\d{1,4}$/i.test(s)
}
