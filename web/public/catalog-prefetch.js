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
  if (app === 'market') {
    // The photos the first screen shows (the Best sellers rail, else the first grid cards) are the
    // largest paint on the page. Announce them as soon as the catalog arrives — before the app has
    // even downloaded — so the browser fetches them first instead of last.
    res.then(function (text) {
      try {
        var items = (JSON.parse(text).items || [])
        var lead = items.filter(function (i) {
          return i.thumb_url && (i.badges || []).indexOf('best_seller') >= 0 && i.stock_status !== 'out_of_stock'
        }).slice(0, 3)
        if (lead.length < 2) lead = items.filter(function (i) { return i.thumb_url }).slice(0, 4)
        lead.forEach(function (i, n) {
          var l = document.createElement('link')
          l.rel = 'preload'
          l.as = 'image'
          l.href = i.thumb_url
          if (n === 0) l.setAttribute('fetchpriority', 'high')
          document.head.appendChild(l)
        })
      } catch (e) { /* ignore */ }
    }).catch(function () {})
  }
})()
