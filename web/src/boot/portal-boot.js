// The portal's first frame (ship 2, item 14), decided before the first paint. The PORTAL build's
// index.html runs this file first — vite.config.ts portalHtml() emits it as a hashed /assets file and
// puts it ahead of catalog-prefetch.js; the market build never loads it. A file, never an inline
// <script>: the site's CSP (script-src 'self') blocks inline scripts. On <html> it sets:
//
//   data-theme      the saved theme (lib/theme.tsx 'yq-theme'), so a dark user's first frame is dark
//   data-boot       which frame of the served #yq-boot node shows (src/boot/frames.ts):
//                     off    /c /o /f /optout — the public pages (and their light look: no theme either)
//                     plain  /invite
//                     night  /login, or any other page with no stored session (it is going to /login)
//                     sales  a stored session on a phone whose last user was a salesman
//                     shell  a stored session, anyone else
//   data-boot-rail  narrow when that shell's rail is collapsed (AppShell 'yq-collapsed',
//                   SalesmanShell 'yq-sales-rail')
//
// MIRRORS (change together): src/boot/handoff.ts bootKindFor() — the same paths (main.tsx sends /c and
// /o to PublicApp; App.tsx routes /f, /optout and /invite outside ProtectedRoute), the same session test
// (lib/supabase.ts storedSession(): an sb-<ref>-auth-token holding an access token) and the same
// remembered role (lib/auth.tsx writeCachedMe() 'yq-role'). Plain ES5, no modules. Blocked storage: no
// session known, so the night; the theme stays light.
;(function () {
  var html = document.documentElement
  function get(key) {
    try {
      return window.localStorage.getItem(key)
    } catch (e) {
      return null
    }
  }
  function signedIn() {
    try {
      var ls = window.localStorage
      for (var i = 0; i < ls.length; i++) {
        var key = ls.key(i)
        if (!key || !/^sb-.+-auth-token$/.test(key)) continue
        var raw = JSON.parse(ls.getItem(key) || 'null')
        var s = raw && (raw.currentSession || raw)
        if (s && s.access_token) return true
      }
    } catch (e) { /* blocked storage or a corrupt value: not signed in, as far as a first frame knows */ }
    return false
  }
  var path = location.pathname
  var kind
  if (/^\/(c|o|f|optout)\/[^/]/.test(path)) kind = 'off'
  else if (/^\/invite\/?$/.test(path)) kind = 'plain'
  else if (/^\/login\/?$/.test(path) || !signedIn()) kind = 'night'
  else kind = get('yq-role') === 'salesman' ? 'sales' : 'shell'
  html.setAttribute('data-boot', kind)
  if (kind === 'off') return
  html.setAttribute('data-theme', get('yq-theme') === 'dark' ? 'dark' : 'light')
  if ((kind === 'sales' ? get('yq-sales-rail') : get('yq-collapsed')) === '1') html.setAttribute('data-boot-rail', 'narrow')
})()
