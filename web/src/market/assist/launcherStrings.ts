import { locale } from '../i18n'

/** The launcher's own words: kept apart so the first download never pulls in the chat's strings. */
export const L =
  locale.lang === 'ar'
    ? { text: 'اسأل YQ', label: 'افتح YQ Assist', ask: 'اسأل المساعد', ready: 'إجابتك جاهزة — افتح YQ Assist' }
    : { text: 'Ask YQ', label: 'Open YQ Assist', ask: 'Ask the assistant', ready: 'Your answer is ready — open YQ Assist' }
