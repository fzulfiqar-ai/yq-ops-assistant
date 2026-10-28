import { locale } from '../i18n'

/** The launcher is the only part of the assistant in the first download, so its two words live apart from strings.ts. */
export const L = locale.lang === 'ar' ? { text: 'اسأل YQ', label: 'افتح مساعد YQ' } : { text: 'Ask YQ', label: 'Open YQ Assistant' }
