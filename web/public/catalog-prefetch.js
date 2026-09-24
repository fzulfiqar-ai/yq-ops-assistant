// A catalog link asks the API for the products straight away, in parallel with downloading the
// app, instead of after the app has loaded and mounted — seconds later on a phone.
// lib/shopApi.ts getCatalog() / market/lib/marketApi.ts getMarket() pick up window.__yqCatalog.
//
//   portal build   /c/{token}[?ref=]   → /public/catalog/{token}[?ref=]
//   market build   any page            → /api/market[?ref=]     (data-app="market" on this tag)
//                                        the SAME-ORIGIN edge copy (web/workers/market.js: 60 s fresh,
//                                        stale while it revalidates, the last good copy when the API is
//                                        cold), falling back to the API's /public/market[?ref=] when the
//                                        answer is not JSON (no Worker in front: local preview, an old
//                                        host) or fails. getMarket() matches on the /api/market key and
//                                        reads `src` (edge-hit / edge-stale / … / api) for the RUM beacon.
//
// A separate same-origin file, never an inline <script>: the site's Content-Security-Policy
// (script-src 'self', web/vercel.json) blocks inline scripts, which silently disabled the
// first version of this. index.html passes the API base in data-api. Plain ES5, no modules.
;(function () {
  var el = document.currentScript
  var api = el && el.getAttribute('data-api')
  if (!api || api.charAt(0) === '%' || !window.fetch) return
  var app = el && el.getAttribute('data-app')
  var params = new URLSearchParams(location.search)
  var url = null
  var direct = null
  if (app === 'market') {
    var ref = (params.get('ref') || '').toLowerCase()
    if (!ref) {
      // /{slug} → that rep's storefront; otherwise the rep this phone remembers (90 days)
      var seg = location.pathname.split('/').filter(Boolean)
      var reserved = { search: 1, cart: 1, checkout: 1, orders: 1, p: 1, t: 1, o: 1, c: 1, join: 1, shop: 1, me: 1, quick: 1, about: 1, help: 1, ask: 1, saved: 1, brands: 1, wekome: 1, 'coming-soon': 1 }
      if (seg.length === 1 && !reserved[seg[0].toLowerCase()] && /^[a-z0-9][a-z0-9-]{1,31}$/.test(seg[0].toLowerCase())) {
        ref = seg[0].toLowerCase()
      } else {
        try {
          var saved = JSON.parse(localStorage.getItem('yq-ref') || 'null')
          if (saved && saved.slug && Date.now() - Number(saved.ts || 0) < 90 * 86400000) ref = String(saved.slug)
        } catch (e) { /* ignore */ }
      }
    }
    var qs = ref ? '?ref=' + encodeURIComponent(ref) : ''
    url = '/api/market' + qs                 // MIRRORS market/lib/marketApi.ts marketPath()
    direct = api + '/public/market' + qs
  } else {
    var m = location.pathname.match(/^\/c\/([^/]+)\/?$/)
    if (!m) return
    var r = params.get('ref')
    url = api + '/public/catalog/' + m[1] + (r ? '?ref=' + encodeURIComponent(r) : '')
  }
  var early = { url: url, res: null, src: 'pre' }
  function text(r) { return r.ok ? r.text() : Promise.reject(r.status) }
  function json(r) { return r.ok && (r.headers.get('content-type') || '').indexOf('application/json') >= 0 }
  var res = direct
    ? fetch(url).then(function (r) {
        if (!json(r)) return Promise.reject(r.status)   // index.html: no Worker in front of this host
        early.src = 'pre-edge-' + String(r.headers.get('x-yq-cache') || 'miss').toLowerCase()
        return r.text()
      }).catch(function () {
        early.src = 'pre-api'
        return fetch(direct).then(text)
      })
    : fetch(url).then(text)
  res.catch(function () {})
  early.res = res
  window.__yqCatalog = early
  if (app !== 'market') return

  // The largest paint of the home page is promo slide 1: its uploaded image, or the centre photo of
  // its composed creative. Announce that ONE request as soon as the catalog arrives — before the app
  // has even downloaded — with the SAME src / srcset / sizes the app will use, so the browser reuses
  // the preload instead of fetching twice. One only: a second preload competes with it on slow 4G,
  // and everything else on the first screen (the category tiles) is decided by the app.
  //
  // MIRRORS (change together): web/src/market/lib/slides.ts buildSlides() slide-1 rules,
  // firstSlideImage(), SLIDE_SIZES / SLIDE_ART_SIZES / SLIDE_THUMB_SIZES and thumbSrcset();
  // lib/home.ts dealSets() / lastChance() / hasRealDrop() (heroSplit() keeps slide 1 on the desktop
  // stage, so desktop and phone share slide 1);
  // lib/format.ts marginOf() / priceAnchor(); ui/ProductImage.tsx srcset;
  // MarketContext.tsx campaign audience filter + lib/device.ts isRecognized(). The desktop layout
  // (hero stage + tiles) starts at 1024px — shell/useViewport.ts isDesktopLike, as pages/Home.tsx uses.
  var SLIDE_SIZES = { hero: '(min-width: 1440px) 1000px, (min-width: 1024px) 900px, 92vw', phone: '(min-width: 768px) 56vw, 92vw' }
  var SLIDE_ART_SIZES = { hero: '(min-width: 1440px) 420px, (min-width: 1024px) 380px, 40vw', phone: '(min-width: 768px) 22vw, 34vw' }
  var SLIDE_THUMB_SIZES = { hero: '(min-width: 1440px) 208px, (min-width: 1024px) 184px, 20vw', phone: '(min-width: 768px) 12vw, 17vw' }

  function has(i, badge) { return (i.badges || []).indexOf(badge) >= 0 }
  function live(i) { return i.stock_status !== 'out_of_stock' }
  function photo(i) { return Boolean((i.thumb_urls && i.thumb_urls['320']) || i.thumb_url || i.product_image_url) }
  function num(v) { return v == null ? NaN : Number(v) }
  function realDrop(i) {
    var price = num(i.price_bhd)
    var was = num(i.was_bhd)
    return isFinite(price) && isFinite(was) && was > price
  }
  function marginPct(i) {
    var raw = i.compare_at_bhd != null ? i.compare_at_bhd : i.b2c_bhd
    var price = num(i.price_bhd)
    var retail = num(raw)
    if (!isFinite(price) || !isFinite(retail) || !(price > 0) || !(retail > price)) return null
    var margin = Math.round((retail - price) * 1000) / 1000
    return Math.round((margin / retail) * 100)
  }
  function recognized() {
    try {
      var orders = JSON.parse(localStorage.getItem('yq-orders') || '[]') || []
      for (var k = 0; k < orders.length; k++) if (orders[k] && typeof orders[k].token === 'string' && orders[k].token.length >= 16) return true
    } catch (e) { /* ignore */ }
    try {
      var who = JSON.parse(localStorage.getItem('yq-shop-customer') || 'null')
      if (who && who.phone) return true
    } catch (e) { /* ignore */ }
    return false
  }
  // dealSets(items, offers): drops / live offers / bundle codes reserved first, last chance after
  function deals(items, offers) {
    var bundle = {}
    ;(offers || []).forEach(function (o) {
      var kind = String(o.kind || '').toLowerCase()
      var end = o.ends_at ? new Date(o.ends_at).getTime() : NaN
      if ((o.ends_at && !isNaN(end) && end <= Date.now()) || !(kind === 'bundle_price' || kind.indexOf('bundle') >= 0)) return
      ;(o.scope_codes || []).forEach(function (code) { bundle[code] = 1 })
    })
    var taken = {}
    var drops = []
    items.forEach(function (i) {
      if (!live(i)) return
      var isDrop = realDrop(i)
      if (isDrop) drops.push(i)
      if (isDrop || bundle[i.item_code] || has(i, 'on_offer')) taken[i.item_code] = 1
    })
    var last = items
      .map(function (item, index) { return { item: item, index: index, pct: marginPct(item) } })
      .filter(function (x) { return live(x.item) && has(x.item, 'clearance') })
      .sort(function (a, b) {
        if (a.pct != null && b.pct != null && a.pct !== b.pct) return b.pct - a.pct
        if ((a.pct == null) !== (b.pct == null)) return a.pct == null ? 1 : -1
        return a.index - b.index
      })
      .map(function (x) { return x.item })
    return { drops: drops, last: last.filter(function (i) { return !taken[i.item_code] }) }
  }
  // buildSlides() slide 1, as { campaign, lead }: the first hero-then-strip campaign for this
  // visitor, else the first data slide the catalog backs; lead = the photo its creative leads with.
  // Order again always goes second, so it never decides slide 1.
  function slideOne(data) {
    var items = data.items || []
    var rec = recognized()
    var byCode = {}
    items.forEach(function (i) { byCode[i.item_code] = i })
    var campaign = null
    var rank = 2
    ;(data.campaigns || []).forEach(function (c) {
      var p = c.placement || []
      var at = p.indexOf('hero') >= 0 ? 0 : p.indexOf('strip') >= 0 ? 1 : 2
      if (at < rank && (c.audience === 'all' || (c.audience === 'recognized') === rec)) { campaign = c; rank = at }
    })
    var first = function (pool) { return pool.filter(photo)[0] || null }
    if (campaign) {
      var codes = (campaign.product_codes || []).map(function (code) { return byCode[code] }).filter(Boolean)
      return { campaign: campaign, lead: first(codes) }
    }
    var liveItems = items.filter(live)
    var d = deals(items, null)
    var best = liveItems.filter(function (i) { return has(i, 'best_seller') })
    var fresh = liveItems.filter(function (i) { return has(i, 'new') })
    var moving = liveItems.filter(function (i) { return has(i, 'trending') || has(i, 'selling_fast') })
    var pool = d.last.length >= 3 ? d.last : d.drops.length >= 2 ? d.drops : best.length >= 3 ? best : fresh.length >= 3 ? fresh : moving.length >= 3 ? moving : best.length ? best : liveItems
    return { campaign: null, lead: first(pool) }
  }
  // ui/ProductImage.tsx: the WebP set as srcset (160/320/512), else the first legacy photo
  function thumb(i, sizes) {
    var set = i.thumb_urls
    if (set && set['320']) return { src: set['320'], srcset: (set['160'] || set['320']) + ' 160w, ' + set['320'] + ' 320w, ' + (set['512'] || set['320']) + ' 512w', sizes: sizes }
    var src = i.thumb_url || i.product_image_url || i.package_image_url
    return src ? { src: src, sizes: sizes } : null
  }
  // firstSlideImage()
  function slideImage(s, size) {
    var c = s && s.campaign
    if (c && c.image_url) {
      var cover = c.image_fit === 'cover'
      return { src: c.image_url, srcset: c.image_url_600 ? c.image_url_600 + ' 600w, ' + c.image_url + ' 1200w' : null, sizes: (cover ? SLIDE_SIZES : SLIDE_ART_SIZES)[size] }
    }
    return s && s.lead ? thumb(s.lead, SLIDE_THUMB_SIZES[size]) : null
  }
  function preload(img, high) {
    if (!img || !img.src) return
    var l = document.createElement('link')
    l.rel = 'preload'
    l.as = 'image'
    l.href = img.src
    if (img.srcset) {
      l.setAttribute('imagesrcset', img.srcset)
      l.setAttribute('imagesizes', img.sizes)
    }
    if (high) l.setAttribute('fetchpriority', 'high')
    document.head.appendChild(l)
  }

  res.then(function (text) {
    try {
      var data = JSON.parse(text)
      var desktop = window.matchMedia ? window.matchMedia('(min-width: 1024px)').matches : window.innerWidth >= 1024
      preload(slideImage(slideOne(data), desktop ? 'hero' : 'phone'), true)
    } catch (e) { /* ignore */ }
  }).catch(function () {})
})()
