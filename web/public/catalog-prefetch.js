// A catalog link (/c/{token}) asks the API for the products straight away, in parallel
// with downloading the app, instead of after the app has loaded and mounted — seconds
// later on a phone. lib/shopApi.ts getCatalog() picks up window.__yqCatalog.
//
// A separate same-origin file, never an inline <script>: the site's Content-Security-Policy
// (script-src 'self', web/vercel.json) blocks inline scripts, which silently disabled the
// first version of this. index.html passes the API base in data-api.
;(function () {
  var el = document.currentScript
  var api = el && el.getAttribute('data-api')
  var m = location.pathname.match(/^\/c\/([^/]+)\/?$/)
  if (!m || !api || api.charAt(0) === '%' || !window.fetch) return
  var ref = new URLSearchParams(location.search).get('ref')
  var url = api + '/public/catalog/' + m[1] + (ref ? '?ref=' + encodeURIComponent(ref) : '')
  var res = fetch(url).then(function (r) {
    return r.ok ? r.text() : Promise.reject(r.status)
  })
  res.catch(function () {})
  window.__yqCatalog = { url: url, res: res }
})()
