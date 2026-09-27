// The marketplace's language, decided before the first paint (market build only — vite.config.ts
// marketHtml() puts this tag in the <head>, ahead of the stylesheet's first use and of the app):
//
//   ?lang=ar|en  →  the choice this phone saved (localStorage 'yq-lang')  →  the phone's FIRST language
//
// It sets <html lang dir> so the first frame is already right-to-left for an Arabic reader (no
// English frame flipping over), writes the answer to data-lang for the app to read back
// (web/src/market/i18n/index.ts), keeps a ?lang= it was given as this phone's choice, and on an
// Arabic page starts the two Arabic font files at once (English pages never ask for them: their
// @font-face is only in the Arabic font stack, market.css).
//
// A separate same-origin file, never an inline <script>: the site's Content-Security-Policy
// (script-src 'self', public/_headers) blocks inline scripts. Plain ES5, no modules.
// MIRRORS web/src/market/i18n/index.ts pickLang() and LANG_KEY — change both together.
;(function () {
  var KEY = 'yq-lang'
  function norm(v) {
    var s = String(v || '').replace(/^\s+|\s+$/g, '').toLowerCase()
    if (s === 'ar' || s.indexOf('ar-') === 0) return 'ar'
    if (s === 'en' || s.indexOf('en-') === 0) return 'en'
    return null
  }
  var fromUrl = null
  try {
    fromUrl = norm(new URLSearchParams(location.search).get('lang'))
  } catch (e) { /* no URLSearchParams: the saved choice and the phone decide */ }
  var saved = null
  try {
    if (fromUrl) localStorage.setItem(KEY, fromUrl)
    saved = norm(localStorage.getItem(KEY))
  } catch (e) { /* storage blocked */ }
  var first = (navigator.languages && navigator.languages[0]) || navigator.language || ''
  var lang = fromUrl || saved || (norm(first) === 'ar' ? 'ar' : 'en')
  var root = document.documentElement
  root.setAttribute('lang', lang)
  root.setAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
  root.setAttribute('data-lang', lang)
  if (lang !== 'ar') return
  var fonts = ['/fonts/ibm-plex-sans-arabic-400-v1.woff2', '/fonts/ibm-plex-sans-arabic-600-v1.woff2']
  for (var i = 0; i < fonts.length; i++) {
    var l = document.createElement('link')
    l.rel = 'preload'
    l.as = 'font'
    l.type = 'font/woff2'
    l.crossOrigin = 'anonymous'
    l.href = fonts[i]
    document.head.appendChild(l)
  }
})()
