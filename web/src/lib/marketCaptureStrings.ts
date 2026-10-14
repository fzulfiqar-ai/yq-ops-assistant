import type { MiKind, MiResult } from './marketIntel'

/**
 * The Spotted sheet's words in English and Arabic (release r8m, plan F3). The portal is English-only;
 * this one sheet is what a rep uses in a shop, so it carries its own small table and an EN / AR
 * toggle, and reads right to left in Arabic. The choice is kept on the phone (`yq-mi-lang`); without
 * one, a phone whose first language is Arabic opens in Arabic. Numbers stay Western digits in both,
 * as the marketplace prints them. Product names and codes are English (the catalog) and are shown
 * left to right inside the Arabic sheet.
 */

export type CaptureLang = 'en' | 'ar'
export type CaptureTile = 'quality' | 'packaging' | 'asked' | 'competitor' | 'trend' | 'other'

export interface CaptureStrings {
  dir: 'ltr' | 'rtl'
  /** the other language, on the toggle */
  switchTo: string
  switchAria: string
  spotted: string
  buttonAria: string
  buttonWaiting: (n: number) => string
  titleEdit: string
  titleResult: string
  titleQueued: string
  subtitle: string
  whatIsIt: string
  tiles: Record<CaptureTile, string>
  whichProduct: string
  searchProducts: string
  atShop: string
  recent: string
  products: string
  results: string
  noProducts: string
  productsFailed: string
  loading: string
  notSure: string
  notSureName: string
  change: string
  pickFromList: string
  whatsWrong: string
  noIssueTypes: string
  safetyChip: string
  safetyHint: string
  howMany: string
  qtyLess: string
  qtyMore: string
  basis: string
  customerSaid: string
  iSaw: string
  labelPhoto: string
  labelPhotoAsk: string
  labelBadge: string
  batch: string
  packVersion: string
  compPrice: string
  compPromo: string
  fieldTitle: Record<MiKind, string>
  optional: string
  brand: string
  pickOrType: string
  price: string
  currency: string
  category: string
  anyCategory: string
  barcode: string
  barcodeHint: string
  howMuch: string
  demand: Record<'low' | 'medium' | 'high', string>
  qty: string
  note: string
  notePlaceholder: string
  speak: string
  stopSpeaking: string
  listening: string
  voiceLang: string
  shop: string
  thisShop: string
  changeShop: string
  notAtShop: string
  addShop: string
  pickShopAria: string
  loadingShops: string
  pickShop: string
  camera: string
  another: string
  gallery: string
  photoAlt: (n: number) => string
  removePhoto: (n: number) => string
  preparingPhoto: string
  upTo: (n: number) => string
  photoUnreadable: string
  save: string
  saving: string
  preparing: string
  saveFailed: string
  spotAnother: string
  done: string
  seeMine: string
  queuedTitle: string
  queuedBody: string
  waiting: (n: number) => string
  retryNow: string
  notSaved: string
  discard: string
  result: Record<MiResult, string>
  resultLine: Record<MiResult, string>
  seenIn: (n: number) => string
  yqCode: string
  legacy: string
  reportReceived: string
  reportReceivedBody: string
  /** the report joined an open case of the same product and issue (the API's case.joined) */
  joinedTitle: string
  joinedBody: (shops: number) => string
  alerted: string
  alertedBody: string
  safetyNoted: string
  safetyQueued: string
}

const en: CaptureStrings = {
  dir: 'ltr',
  switchTo: 'العربية',
  switchAria: 'Show this form in Arabic',
  spotted: 'Spotted',
  buttonAria: 'Spotted: capture what you saw in the market',
  buttonWaiting: (n) => `Spotted: ${n} waiting to send`,
  titleEdit: 'What did you spot?',
  titleResult: 'Thanks — got it',
  titleQueued: 'Kept on this phone',
  subtitle: 'Photos of products and shelves, not people. Everything but the photo is optional.',
  whatIsIt: 'What is it?',
  tiles: {
    quality: 'Quality problem',
    packaging: 'Packaging',
    asked: 'Customer asked for',
    competitor: 'Competitor',
    trend: 'New product / trend',
    other: 'Other',
  },
  whichProduct: 'Which product?',
  searchProducts: 'Search name or code',
  atShop: 'At this shop',
  recent: 'Recent',
  products: 'Products',
  results: 'Results',
  noProducts: 'No product matches. Try another word, or tap Not sure.',
  productsFailed: 'Products could not load. Tap Not sure and type the name.',
  loading: 'Loading…',
  notSure: 'Not sure / not ours',
  notSureName: 'What is it? (name on the pack)',
  change: 'Change',
  pickFromList: 'Pick from the list',
  whatsWrong: "What's wrong?",
  noIssueTypes: 'Say what is wrong in the note.',
  safetyChip: 'Heat / smoke / swelling / shock',
  safetyHint: 'A safety report goes to the manager at once.',
  howMany: 'How many affected?',
  qtyLess: 'One fewer',
  qtyMore: 'One more',
  basis: 'Who saw the problem?',
  customerSaid: 'Customer said',
  iSaw: 'I saw it',
  labelPhoto: 'Label / batch',
  labelPhotoAsk: 'Add a photo of the label or batch code if you can.',
  labelBadge: 'Label',
  batch: 'Batch or lot code',
  packVersion: 'Packaging version',
  compPrice: 'Their price',
  compPromo: 'Their promotion',
  fieldTitle: {
    new_product: 'Name or code on the pack',
    competitor_price: 'Which product',
    promotion: "What's on offer",
    shop_asked: 'What they asked for (name or YQ code)',
    complaint: 'Which product (name or YQ code)',
    other: 'Product (optional)',
  },
  optional: 'Optional',
  brand: 'Brand',
  pickOrType: 'Pick or type',
  price: 'Price',
  currency: 'BHD',
  category: 'Category',
  anyCategory: 'Any',
  barcode: 'Barcode digits',
  barcodeHint: 'Optional, from the pack',
  howMuch: 'How much do they want?',
  demand: { low: 'Low', medium: 'Medium', high: 'High' },
  qty: 'Qty',
  note: 'Note',
  notePlaceholder: 'What did you see? Type it, or tap the mic and say it.',
  speak: 'Speak',
  stopSpeaking: 'Stop',
  listening: 'Listening…',
  voiceLang: 'Voice language',
  shop: 'Shop',
  thisShop: 'This shop',
  changeShop: 'Change',
  notAtShop: 'Not at a shop',
  addShop: 'Add the shop (optional)',
  pickShopAria: 'Pick the shop',
  loadingShops: 'Loading your shops…',
  pickShop: 'Pick one of your shops',
  camera: 'Camera',
  another: 'Another',
  gallery: 'Gallery',
  photoAlt: (n) => `Photo ${n}`,
  removePhoto: (n) => `Remove photo ${n}`,
  preparingPhoto: 'Preparing photo',
  upTo: (n) => `Up to ${n} photos per sighting.`,
  photoUnreadable: 'Could not read that photo.',
  save: 'Save',
  saving: 'Saving…',
  preparing: 'Preparing photo…',
  saveFailed: 'Could not save. Please try again.',
  spotAnother: 'Spot another',
  done: 'Done',
  seeMine: 'See my reports',
  queuedTitle: 'No signal right now.',
  queuedBody: 'Your sighting is saved on this phone and sends by itself when the connection is back, as long as the app stays open.',
  waiting: (n) => `${n} sighting${n === 1 ? '' : 's'} waiting for signal. Keep the app open.`,
  retryNow: 'Retry now',
  notSaved: 'Not saved:',
  discard: 'Discard',
  result: {
    already_in_yq: 'Already in YQ',
    seen_before: 'Seen before',
    new_find: 'New market find',
    saved_for_review: 'Saved: office will identify',
  },
  resultLine: {
    already_in_yq: 'We stock this product.',
    seen_before: 'The team has seen this before; yours is added to it.',
    new_find: 'Something new: the office will look at it.',
    saved_for_review: 'The office will identify it from the photo.',
  },
  seenIn: (n) => `seen in ${n} shop${n === 1 ? '' : 's'}`,
  yqCode: 'YQ code',
  legacy: 'Saved to the Finds board while Market Intel is being set up.',
  reportReceived: 'Report received',
  reportReceivedBody: 'The office checks it. You see what happens in My reports.',
  joinedTitle: 'Added to an open report',
  joinedBody: (shops) =>
    `The same problem was already reported${shops ? `, now from ${shops} shop${shops === 1 ? '' : 's'}` : ''}. The office reviews it as one case; you see what happens in My reports.`,
  alerted: 'Manager alerted',
  alertedBody: 'Keep the item aside if you can.',
  safetyNoted: 'Marked as a safety issue for the office.',
  safetyQueued: 'Safety report: the manager is told as soon as it sends.',
}

/**
 * A count in natural Arabic: one (its own word), two (the dual), 3–10 and 103–110 (plural), 11–99 (singular
 * accusative), 100, 101, 102, 200… (singular): "محلّين", "3 محلات", "12 محلًا" — never "2 محلات".
 */
function arCount(n: number, w: { one: string; two: string; few: string; many: string; hundred: string }): string {
  if (n === 1) return w.one
  if (n === 2) return w.two
  const r = n % 100
  if (r >= 3 && r <= 10) return `${n} ${w.few}`
  if (r >= 11 && r <= 99) return `${n} ${w.many}`
  return `${n} ${w.hundred}`
}
/** after "من" / "في" (genitive) */
const AR_SHOPS = { one: 'محل واحد', two: 'محلّين', few: 'محلات', many: 'محلًا', hundred: 'محل' }
/** as the subject (nominative) */
const AR_SIGHTINGS = { one: 'ملاحظة واحدة', two: 'ملاحظتان', few: 'ملاحظات', many: 'ملاحظة', hundred: 'ملاحظة' }

const ar: CaptureStrings = {
  dir: 'rtl',
  switchTo: 'English',
  switchAria: 'اعرض النموذج بالإنجليزية',
  spotted: 'رصد',
  buttonAria: 'رصد: سجّل ما رأيته في السوق',
  buttonWaiting: (n) => `رصد: ${arCount(n, AR_SIGHTINGS)} بانتظار الإرسال`,
  titleEdit: 'ماذا رأيت؟',
  titleResult: 'شكرًا، وصلت',
  titleQueued: 'محفوظ على هذا الهاتف',
  subtitle: 'صوّر المنتجات والرفوف، لا الأشخاص. كل شيء غير الصورة اختياري.',
  whatIsIt: 'ما هو؟',
  tiles: {
    quality: 'مشكلة جودة',
    packaging: 'التغليف',
    asked: 'طلب الزبون',
    competitor: 'منافس',
    trend: 'منتج جديد / رائج',
    other: 'أخرى',
  },
  whichProduct: 'أي منتج؟',
  searchProducts: 'ابحث بالاسم أو الرمز',
  atShop: 'في هذا المحل',
  recent: 'الأخيرة',
  products: 'المنتجات',
  results: 'النتائج',
  noProducts: 'لا يوجد منتج مطابق. جرّب كلمة أخرى، أو اختر «غير متأكد».',
  productsFailed: 'تعذّر تحميل المنتجات. اختر «غير متأكد» واكتب الاسم.',
  loading: 'جارٍ التحميل…',
  notSure: 'غير متأكد / ليس من منتجاتنا',
  notSureName: 'ما هو؟ (الاسم على العلبة)',
  change: 'تغيير',
  pickFromList: 'اختر من القائمة',
  whatsWrong: 'ما المشكلة؟',
  noIssueTypes: 'اكتب المشكلة في الملاحظة.',
  safetyChip: 'حرارة / دخان / انتفاخ / صعقة',
  safetyHint: 'بلاغ السلامة يصل إلى المدير فورًا.',
  howMany: 'كم قطعة متأثرة؟',
  qtyLess: 'أقل بواحدة',
  qtyMore: 'أكثر بواحدة',
  basis: 'من رأى المشكلة؟',
  customerSaid: 'قال الزبون',
  iSaw: 'رأيتها بنفسي',
  labelPhoto: 'الملصق / الدفعة',
  labelPhotoAsk: 'أضف صورة للملصق أو رقم الدفعة إن أمكن.',
  labelBadge: 'ملصق',
  batch: 'رقم الدفعة',
  packVersion: 'نسخة التغليف',
  compPrice: 'سعرهم',
  compPromo: 'عرضهم',
  fieldTitle: {
    new_product: 'الاسم أو الرمز على العلبة',
    competitor_price: 'أي منتج',
    promotion: 'ما العرض',
    shop_asked: 'ماذا طلبوا (الاسم أو رمز YQ)',
    complaint: 'أي منتج (الاسم أو رمز YQ)',
    other: 'المنتج (اختياري)',
  },
  optional: 'اختياري',
  brand: 'الماركة',
  pickOrType: 'اختر أو اكتب',
  price: 'السعر',
  currency: 'د.ب',
  category: 'الفئة',
  anyCategory: 'أي فئة',
  barcode: 'أرقام الباركود',
  barcodeHint: 'اختياري، من العلبة',
  howMuch: 'كم يريدون؟',
  demand: { low: 'قليل', medium: 'متوسط', high: 'كثير' },
  qty: 'الكمية',
  note: 'ملاحظة',
  notePlaceholder: 'ماذا رأيت؟ اكتب، أو اضغط الميكروفون وتحدّث.',
  speak: 'تحدّث',
  stopSpeaking: 'إيقاف',
  listening: 'أستمع…',
  voiceLang: 'لغة الصوت',
  shop: 'المحل',
  thisShop: 'هذا المحل',
  changeShop: 'تغيير',
  notAtShop: 'لست في محل',
  addShop: 'أضف المحل (اختياري)',
  pickShopAria: 'اختر المحل',
  loadingShops: 'جارٍ تحميل محلاتك…',
  pickShop: 'اختر أحد محلاتك',
  camera: 'الكاميرا',
  another: 'صورة أخرى',
  gallery: 'المعرض',
  photoAlt: (n) => `الصورة ${n}`,
  removePhoto: (n) => `احذف الصورة ${n}`,
  preparingPhoto: 'جارٍ تجهيز الصورة',
  upTo: (n) => `حتى ${n} صور لكل ملاحظة.`,
  photoUnreadable: 'تعذّرت قراءة هذه الصورة.',
  save: 'حفظ',
  saving: 'جارٍ الحفظ…',
  preparing: 'جارٍ تجهيز الصورة…',
  saveFailed: 'تعذّر الحفظ. حاول مرة أخرى.',
  spotAnother: 'رصد آخر',
  done: 'تم',
  seeMine: 'تقاريري',
  queuedTitle: 'لا توجد إشارة الآن.',
  queuedBody: 'ملاحظتك محفوظة على هذا الهاتف وتُرسل تلقائيًا عند عودة الاتصال، ما دام التطبيق مفتوحًا.',
  waiting: (n) => `${arCount(n, AR_SIGHTINGS)} بانتظار الإشارة. أبقِ التطبيق مفتوحًا.`,
  retryNow: 'أعد المحاولة',
  notSaved: 'لم يُحفظ:',
  discard: 'تجاهل',
  result: {
    already_in_yq: 'موجود لدى YQ',
    seen_before: 'شوهد من قبل',
    new_find: 'اكتشاف جديد في السوق',
    saved_for_review: 'حُفظ: المكتب سيتعرّف عليه',
  },
  resultLine: {
    already_in_yq: 'هذا المنتج متوفر لدينا.',
    seen_before: 'رآه الفريق من قبل، وأُضيفت ملاحظتك إليه.',
    new_find: 'شيء جديد: سيطّلع عليه المكتب.',
    saved_for_review: 'سيتعرّف المكتب عليه من الصورة.',
  },
  seenIn: (n) => `شوهد في ${arCount(n, AR_SHOPS)}`,
  yqCode: 'رمز YQ',
  legacy: 'حُفظ في لوحة الاكتشافات ريثما يُجهَّز نظام معلومات السوق.',
  reportReceived: 'وصل البلاغ',
  reportReceivedBody: 'سيراجعه المكتب، وترى ما يحدث في «تقاريري».',
  joinedTitle: 'أُضيف إلى بلاغ مفتوح',
  joinedBody: (shops) =>
    `هذه المشكلة مُبلَّغ عنها من قبل${shops ? `، والآن من ${arCount(shops, AR_SHOPS)}` : ''}. يراجعها المكتب كحالة واحدة، وترى ما يحدث في «تقاريري».`,
  alerted: 'تم تنبيه المدير',
  alertedBody: 'احتفظ بالقطعة جانبًا إن أمكن.',
  safetyNoted: 'سُجّل كمشكلة سلامة للمكتب.',
  safetyQueued: 'بلاغ سلامة: يُبلَّغ المدير فور إرساله.',
}

export const CAPTURE_STRINGS: Record<CaptureLang, CaptureStrings> = { en, ar }

const LANG_KEY = 'yq-mi-lang'

/** The sheet's language: the phone's saved choice, else Arabic for an Arabic-first phone, else English. */
export function readCaptureLang(): CaptureLang {
  try {
    const saved = localStorage.getItem(LANG_KEY)
    if (saved === 'ar' || saved === 'en') return saved
  } catch {
    /* storage blocked: the phone decides */
  }
  const first = typeof navigator !== 'undefined' ? String(navigator.languages?.[0] || navigator.language || '') : ''
  return first.toLowerCase().startsWith('ar') ? 'ar' : 'en'
}

export function saveCaptureLang(lang: CaptureLang): void {
  try {
    localStorage.setItem(LANG_KEY, lang)
  } catch {
    /* the choice lasts this visit */
  }
}
