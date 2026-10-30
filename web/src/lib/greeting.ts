/**
 * The portal's one greeting: the office top bar and the Dashboard's heading read it, so the two never disagree
 * (they used to turn to "Good evening" an hour apart, at 17:00 and 18:00). The hours are the salesman app's too
 * (pages/sales/lib.ts greeting): morning before 12, afternoon before 17, evening after.
 */
export function daypart(at: Date = new Date()): string {
  const h = at.getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

/** The first word of a full name, else the e-mail's local part, with a capital first letter ('' when neither). */
export function firstNameOf(fullName?: string | null, email?: string | null): string {
  const word = ((fullName || '').trim().split(/\s+/)[0] || (email || '').split('@')[0] || '').trim()
  return word ? word.charAt(0).toUpperCase() + word.slice(1) : ''
}

/** "Good afternoon, Sara" — or the daypart alone when there is no name. */
export function greeting(name?: string | null, at?: Date): string {
  const part = daypart(at)
  return name ? `${part}, ${name}` : part
}
