import { ltr } from '../i18n'
import { A } from './strings'

/** The rep's WhatsApp link with its prefilled text replaced by the merchant's own question and the items discussed. */
export function whatsappWith(url: string, question: string, codes: string[]): string {
  const bits = [`${A.handoffHi}: "${question.slice(0, 160)}"`]
  if (codes.length) bits.push(`${A.handoffItems}: ${codes.slice(0, 3).map(ltr).join(', ')}`)
  const text = bits.join('\n')
  try {
    const u = new URL(url)
    u.searchParams.delete('text')
    const base = u.toString()
    return `${base}${base.includes('?') ? '&' : '?'}text=${encodeURIComponent(text)}`
  } catch {
    return url
  }
}
