/**
 * Every word the marketplace shows — the one import every component uses (`S`, `locale`). The
 * words live in i18n/en.ts and i18n/ar.ts; i18n/index.ts picks the language once per page load
 * (?lang= → the saved choice → the phone's language) and `locale` carries it with its direction.
 * Keep sentences short — most readers are on a phone, in a shop, between customers.
 */
export { locale, plural, S } from './i18n'
