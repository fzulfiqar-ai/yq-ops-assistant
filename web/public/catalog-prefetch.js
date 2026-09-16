// A catalog link asks the API for the products straight away, in parallel with downloading the
// app, instead of after the app has loaded and mounted — seconds later on a phone.
// lib/shopApi.ts getCatalog() / market/lib/marketApi.ts getMarket() pick up window.__yqCatalog.
//
//   portal build   /c/{token}[?ref=]   → /public/catalog/{token}[?ref=]
//   market build   any page            → /public/market[?ref=]  (data-app="market" on this tag)
//
// A separate same-origin file, never an inline <script>: the site's Content-Security-Policy
// (script-src 'self', web/vercel.json) blocks inline scripts, which silently disabled the
// first version of this. index.html passes the API base in data-api.
;(function () {
  var el = document.currentScript
  var api = el && el.getAttribute('data-api')
  if (!api || api.charAt(0) === '%' || !window.fetch) return
  var app = el && el.getAttribute('data-app')
  var params = new URLSearchParams(location.search)
  var url = null
  if (app === 'market') {
    var ref = (params.get('ref') || '').toLowerCase()
    if (!ref) {
      // /{slug} → that rep's storefront; otherwise the rep this phone remembers (90 days)
      var seg = location.pathname.split('/').filter(Boolean)
      var reserved = { search: 1, cart: 1, checkout: 1, orders: 1, p: 1, t: 1, o: 1, c: 1, join: 1 }
      if (seg.length === 1 && !reserved[seg[0].toLowerCase()] && /^[a-z0-9][a-z0-9-]{1,31}$/.test(seg[0].toLowerCase())) {
        ref = seg[0].toLowerCase()
      } else {
        try {
          var saved = JSON.parse(localStorage.getItem('yq-ref') || 'null')
          if (saved && saved.slug && Date.now() - Number(saved.ts || 0) < 90 * 86400000) ref = String(saved.slug)
        } catch (e) { /* ignore */ }
      }
    }
    url = api + '/public/market' + (ref ? '?ref=' + encodeURIComponent(ref) : '')
  } else {
    var m = location.pathname.match(/^\/c\/([^/]+)\/?$/)
    if (!m) return
    var r = params.get('ref')
    url = api + '/public/catalog/' + m[1] + (r ? '?ref=' + encodeURIComponent(r) : '')
  }
  var res = fetch(url).then(function (r) {
    return r.ok ? r.text() : Promise.reject(r.status)
  })
  res.catch(function () {})
  window.__yqCatalog = { url: url, res: res }
})()
