import { locale } from '../i18n'

/** The launcher's own words: kept apart so the first download never pulls in the chat's strings. 2.1: the line
 * beside the button (peek*), and the first message a tap on it sends (first*). */
export const L =
  locale.lang === 'ar'
    ? {
        text: 'اسأل YQ',
        label: 'افتح YQ Assist',
        ask: 'اسأل المساعد',
        ready: 'إجابتك جاهزة — افتح YQ Assist',
        peekShop: 'تريد أن ترى الأكثر مبيعًا اليوم؟',
        peekItem: 'تريد أن تعرف أي الهواتف يناسبها هذا المنتج؟',
        peekClose: 'ليس الآن',
        firstShop: 'الأكثر مبيعًا الآن',
        firstItem: 'هل يناسب جوالي؟',
      }
    : {
        text: 'Ask YQ',
        label: 'Open YQ Assist',
        ask: 'Ask the assistant',
        ready: 'Your answer is ready — open YQ Assist',
        peekShop: "Want today's best sellers?",
        peekItem: 'Want to know which phones this fits?',
        peekClose: 'Not now',
        firstShop: "What's selling now",
        firstItem: 'Does it fit my phone?',
      }
