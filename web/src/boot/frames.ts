/**
 * The portal's first frame (ship 2, item 14): what the served HTML paints before React exists, and
 * what React paints in the same places until the first real screen is ready.
 *
 * ONE source for both, so the hand-over is pixel-identical by construction:
 *   - vite.config.ts portalHtml() injects PORTAL_BOOT_CSS (a <style> in the head) and bootHtml() (the
 *     #yq-boot node before #root) into the PORTAL build's index.html only — the market build never
 *     sees any of it;
 *   - boot/BootFrame.tsx renders the same parts with the same classes, styled by that same <style>
 *     (it stays in the head for the page's life; only the #yq-boot node is ever removed).
 * boot/portal-boot.js, the portal's first script (the CSP forbids inline ones), picks the frame before
 * the first paint by setting html[data-boot], applies the saved theme (html[data-theme]) and the
 * remembered rail width (html[data-boot-rail]):
 *
 *   night  a signed-out visit (the login, or a protected page about to send you there): the login's
 *          static night — the dome and its lit horizon on the #140F24 field. The intro
 *          (components/ui/arc-preloader-hero.tsx), when it plays, starts on this same frame.
 *   plain  /invite: the flat night its page paints first (pages/AcceptInvite.tsx), BEHIND the app —
 *          nobody there hands it over, so it never covers anything.
 *   shell  a returning office user: the office shell's outline (components/AppShell.tsx: the night
 *          sidebar from 1024 px, the 64 px top bar, the page blocks), in the saved theme.
 *   sales  a returning salesman: the field app's outline (components/SalesmanShell.tsx: the 56 px
 *          header, the rows, the floating tab bar under 768 px, the rail from 1024 px), always light
 *          like the app.
 *   off    the public pages (/c, /o, /f, /optout) and a blocked or failed boot script: no frame.
 *
 * The node takes no taps (pointer-events: none) and hides itself after 10 s if nothing handed it over
 * (an app that never boots), so it can never seal the page. Only transform and opacity ever move.
 *
 * Pure strings, no DOM, no React: vite.config.ts imports this file at build time (tsconfig.node.json).
 */

export type BootKind = 'night' | 'plain' | 'shell' | 'sales'

/** the served node's id (portal-boot.js, boot/handoff.ts) */
export const BOOT_ID = 'yq-boot'
/** the head <style>'s id, so a check can find it */
export const BOOT_CSS_ID = 'yq-boot-css'

// Colours. The office frame paints with the portal's own tokens (src/index.css), so it follows the
// theme and any re-brand of them; the fallbacks are today's light values, for the dev server, where
// the stylesheet arrives after the first paint. The field app pins its own light plum set
// (SalesmanShell SALES_TOKENS), and so does its frame.
const BG = 'hsl(var(--background,250 40% 99%))'
const CARD = 'hsl(var(--card,0 0% 100%))'
const LINE = 'hsl(var(--border,258 30% 92%))'
const MUTED = 'hsl(var(--muted,258 25% 96%))'
const PRIMARY = 'hsl(var(--primary,273 39% 41%))'
const S_BG = 'hsl(36 33% 97%)'
const S_LINE = 'hsl(270 14% 89%)'
const S_BAR = 'hsl(270 16% 92%)'
const S_SOFT = 'hsl(36 25% 95%)'
const S_ACCENT = 'hsl(273 40% 94%)'
/** the office sidebar (AppShell) */
const SIDE = 'linear-gradient(180deg,#2A1259 0%,#190A3A 100%)'

export const PORTAL_BOOT_CSS = [
  // the served node: above the app until handed over, never a tap target, a 10 s safety net (the delay
  // survives the reduced-motion kill switch in index.css, the 200 ms does not — reduced motion gets a cut)
  `#yq-boot{position:fixed;inset:0;z-index:90;pointer-events:none;animation:yqb-out .2s linear 10s both}`,
  `@keyframes yqb-out{to{opacity:0;visibility:hidden}}`,
  `html:not([data-boot]) #yq-boot,html[data-boot=off] #yq-boot{display:none}`,
  `html[data-boot=plain] #yq-boot{z-index:-1;animation:none}`,
  // The frame's base on <body> itself, so a frame the browser paints while it is still parsing — after
  // <body>, before the node (seen once at 4x CPU on a loaded machine) — is the night or the shell's
  // sidebar and bars, never the bare canvas. boot/handoff.ts drops html[data-boot] with the node.
  `html[data-boot=night] body,html[data-boot=plain] body{background:#140F24}`,
  `html[data-boot=shell] body{background:linear-gradient(${LINE},${LINE}) 0 64px/100% 1px no-repeat,${BG}}`,
  `html[data-boot=sales] body{background:linear-gradient(#fff,#fff) 0 0/100% 56px no-repeat,linear-gradient(${S_LINE},${S_LINE}) 0 56px/100% 1px no-repeat,${S_BG}}`,
  `@media(min-width:1024px){html[data-boot=shell] body{background:${SIDE} 0 0/260px 100% no-repeat,linear-gradient(${LINE},${LINE}) 260px 64px/100% 1px no-repeat,${BG}}html[data-boot=shell][data-boot-rail=narrow] body{background-size:76px 100%,100% 1px,auto;background-position:0 0,76px 64px,0 0}html[data-boot=sales] body{background:linear-gradient(#fff,#fff) 0 0/64px 100% no-repeat,linear-gradient(${S_LINE},${S_LINE}) 64px 0/1px 100% no-repeat,${S_BG}}}`,
  `@media(min-width:1280px){html[data-boot=sales]:not([data-boot-rail=narrow]) body{background-size:240px 100%,1px 100%,auto;background-position:0 0,240px 0,0 0}}`,
  // a frame: hidden unless it is the one portal-boot.js picked, or React's copy of it (data-on)
  `.yqb{display:none;position:fixed;inset:0;overflow:hidden;pointer-events:none}`,
  `html[data-boot=night] #yq-boot>.yqb-night,html[data-boot=plain] #yq-boot>.yqb-plain,html[data-boot=shell] #yq-boot>.yqb-shell,html[data-boot=sales] #yq-boot>.yqb-sales,.yqb[data-on]{display:block}`,
  `.yqb i{display:block;position:absolute}`,

  // ── night: the login's static night (pages/Login.tsx paints it as its background) ─────────────────
  // The dome and its lit horizon, drawn once with gradients and shadows — no filter anywhere, nothing
  // that moves. Its geometry is the old animated horizon's resting place (an ellipse 132 % of the
  // viewport, centred on the top edge), so the picture is the one people know.
  `.yqb-night,.yqb-plain{background:#140F24}`,
  `.yqb-night .yqb-dome{left:-16%;width:132%;top:-66%;height:132%;border-radius:50%;background:radial-gradient(closest-side,#1C0B3F 0%,#1C0B3F 77%,#24114F 83%,#4121AA 88.5%,#8657F3 92.6%,#D3BFFF 95.8%,#F4EEFF 98.2%,#FFFFFF 99.7%,rgba(255,255,255,0) 100%);box-shadow:0 0 6px 1px rgba(255,255,255,.7),0 0 34px 6px rgba(165,88,251,.26),0 0 120px 24px rgba(73,34,229,.14)}`,
  // the ONE glow: a soft violet light along the horizon. Hidden in the served frame; the login lets it
  // bloom once (transform + opacity, 900 ms) when it arrives — never a loop
  `.yqb-night .yqb-glow{left:-12%;width:124%;top:34%;height:60%;opacity:0;background:radial-gradient(50% 50% at 50% 50%,rgba(165,88,251,.36) 0%,rgba(120,72,240,.16) 46%,rgba(120,72,240,0) 72%)}`,
  `.yqb-night .yqb-glow[data-in]{animation:yqb-bloom .9s cubic-bezier(.22,1,.36,1) both}`,
  `@keyframes yqb-bloom{from{opacity:0;transform:translate3d(0,-5%,0) scale(.94)}to{opacity:1;transform:none}}`,
  // the vignette and the dark band the footer sits on
  `.yqb-night .yqb-veil{inset:0;background:radial-gradient(120% 80% at 50% -10%,rgba(12,7,32,0),rgba(12,7,32,.65)),linear-gradient(0deg,#0C0720 0,rgba(12,7,32,.85) 128px,rgba(12,7,32,0) 256px)}`,

  // ── shell: the office shell (AppShell) ─────────────────────────────────────────────────────────────
  `.yqb-shell{background:${BG}}`,
  `.yqb-shell .yqb-side{display:none;top:0;bottom:0;left:0;width:260px;background:${SIDE}}`,
  `.yqb-shell .yqb-mark{left:20px;top:24px;width:36px;height:36px;border-radius:12px;background:rgba(255,255,255,.16)}`,
  `.yqb-shell .yqb-name{left:68px;top:29px;width:92px;height:11px;border-radius:6px;background:rgba(255,255,255,.26)}`,
  `.yqb-shell .yqb-name::after{content:"";position:absolute;left:0;top:18px;width:140px;height:7px;border-radius:4px;background:rgba(255,255,255,.13)}`,
  `.yqb-shell .yqb-nav{left:12px;right:12px;top:92px;display:flex;flex-direction:column;gap:4px}`,
  `.yqb-shell .yqb-nav>i{position:relative;height:40px;border-radius:12px}`,
  `.yqb-shell .yqb-nav>i::before{content:"";position:absolute;left:12px;top:11px;width:18px;height:18px;border-radius:6px;background:rgba(255,255,255,.16)}`,
  `.yqb-shell .yqb-nav>i::after{content:"";position:absolute;left:44px;top:16px;width:46%;height:8px;border-radius:4px;background:rgba(255,255,255,.11)}`,
  `.yqb-shell .yqb-nav>i:nth-child(3n+2)::after{width:58%}.yqb-shell .yqb-nav>i:nth-child(3n)::after{width:36%}`,
  `.yqb-shell .yqb-nav>i:first-child{background:rgba(255,255,255,.12)}`,
  `.yqb-shell .yqb-top{left:0;right:0;top:0;height:64px;background:${BG};border-bottom:1px solid ${LINE}}`,
  `.yqb-shell .yqb-btn{left:20px;top:14px;width:36px;height:36px;border-radius:10px;background:${MUTED}}`,
  `.yqb-shell .yqb-title{left:68px;top:25px;width:104px;height:14px;border-radius:7px;background:${LINE}}`,
  `.yqb-shell .yqb-chip{right:66px;top:18px;width:104px;height:28px;border-radius:14px;background:${MUTED};border:1px solid ${LINE}}`,
  `.yqb-shell .yqb-av{right:20px;top:14px;width:36px;height:36px;border-radius:50%;background:${PRIMARY}}`,
  `.yqb-shell .yqb-page{left:0;right:0;top:64px;bottom:0;padding:24px 16px;display:flex;flex-direction:column;gap:14px}`,
  `.yqb-shell .yqb-page>i{position:relative;flex:none}`,
  `.yqb-shell .yqb-h{width:min(52%,240px);height:24px;border-radius:8px;background:${LINE}}`,
  `.yqb-shell .yqb-sub{width:min(78%,360px);height:11px;border-radius:6px;background:${MUTED};margin-bottom:6px}`,
  `.yqb-shell .yqb-tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}`,
  `.yqb-shell .yqb-tiles>i{position:relative;height:100px;border-radius:16px;background:${CARD};border:1px solid ${LINE}}`,
  `.yqb-shell .yqb-tiles>i::before{content:"";position:absolute;left:16px;top:18px;width:44%;height:9px;border-radius:5px;background:${MUTED}}`,
  `.yqb-shell .yqb-tiles>i::after{content:"";position:absolute;left:16px;top:42px;width:62%;height:22px;border-radius:8px;background:${LINE}}`,
  `.yqb-shell .yqb-page>.yqb-card{flex:1 1 auto;min-height:180px;border-radius:16px;background:${CARD};border:1px solid ${LINE}}`,
  `.yqb-shell .yqb-card::before{content:"";position:absolute;left:18px;right:18px;top:20px;height:12px;width:34%;border-radius:6px;background:${LINE}}`,
  `.yqb-shell .yqb-card::after{content:"";position:absolute;left:18px;right:18px;top:56px;bottom:18px;border-radius:10px;background:repeating-linear-gradient(180deg,${MUTED} 0 14px,transparent 14px 34px)}`,
  `@media(min-width:1024px){.yqb-shell .yqb-side{display:block}.yqb-shell .yqb-top,.yqb-shell .yqb-page{left:260px}.yqb-shell .yqb-page{padding:24px 32px}.yqb-shell .yqb-tiles{grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.yqb-shell .yqb-tiles>i{height:112px}}`,
  // the collapsed sidebar (AppShell 'yq-collapsed' = 1): 76 px, icons only
  `@media(min-width:1024px){html[data-boot-rail=narrow] .yqb-shell .yqb-side{width:76px}html[data-boot-rail=narrow] .yqb-shell .yqb-top,html[data-boot-rail=narrow] .yqb-shell .yqb-page{left:76px}html[data-boot-rail=narrow] .yqb-shell .yqb-name,html[data-boot-rail=narrow] .yqb-shell .yqb-nav>i::after{display:none}html[data-boot-rail=narrow] .yqb-shell .yqb-nav>i::before{left:17px}}`,

  // ── sales: the field app (SalesmanShell), always light ─────────────────────────────────────────────
  `.yqb-sales{background:${S_BG}}`,
  `.yqb-sales .yqb-sh{left:0;right:0;top:0;height:56px;background:#fff;border-bottom:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-av{left:16px;top:10px;width:36px;height:36px;border-radius:50%;background:${S_ACCENT}}`,
  `.yqb-sales .yqb-t1{left:62px;top:14px;width:76px;height:12px;border-radius:6px;background:${S_BAR}}`,
  `.yqb-sales .yqb-t2{left:62px;top:33px;width:56px;height:8px;border-radius:4px;background:${S_SOFT};border:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-bell{right:22px;top:17px;width:22px;height:22px;border-radius:8px;background:${S_BAR}}`,
  `.yqb-sales .yqb-sp{left:0;right:0;top:56px;bottom:0;padding:16px;display:flex;flex-direction:column;gap:12px}`,
  `.yqb-sales .yqb-sp>i{position:relative;flex:none}`,
  `.yqb-sales .yqb-search{height:48px;border-radius:14px;background:#fff;border:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-search::before{content:"";position:absolute;left:16px;top:18px;width:40%;height:10px;border-radius:5px;background:${S_BAR}}`,
  `.yqb-sales .yqb-chips{height:32px;display:flex;gap:8px}`,
  `.yqb-sales .yqb-chips>i{position:relative;width:84px;height:32px;border-radius:16px;background:#fff;border:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-chips>i:first-child{background:${S_ACCENT};border-color:${S_ACCENT}}`,
  `.yqb-sales .yqb-row{height:84px;border-radius:16px;background:#fff;border:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-row::before{content:"";position:absolute;left:12px;top:12px;width:58px;height:58px;border-radius:12px;background:${S_SOFT};box-shadow:inset 0 0 0 1px ${S_LINE}}`,
  `.yqb-sales .yqb-row::after{content:"";position:absolute;left:84px;right:18px;top:20px;height:44px;background:linear-gradient(${S_BAR} 0 11px,transparent 11px 22px,${S_SOFT} 22px 31px,transparent 31px) 0 0/62% 100% no-repeat}`,
  `.yqb-sales .yqb-tabs{left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom,0px));height:64px;border-radius:24px;background:#fff;border:1px solid ${S_LINE};box-shadow:0 12px 32px -12px rgba(25,20,36,.28);display:flex}`,
  `.yqb-sales .yqb-tabs>i{position:relative;flex:1}`,
  `.yqb-sales .yqb-tabs>i::before{content:"";position:absolute;left:50%;top:12px;width:22px;height:22px;margin-left:-11px;border-radius:8px;background:${S_BAR}}`,
  `.yqb-sales .yqb-tabs>i::after{content:"";position:absolute;left:50%;top:42px;width:30px;height:6px;margin-left:-15px;border-radius:3px;background:${S_SOFT};border:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-tabs>i:first-child::before{background:${S_ACCENT};box-shadow:0 0 0 7px ${S_ACCENT}}`,
  `.yqb-sales .yqb-rail{display:none;left:0;top:0;bottom:0;width:64px;background:#fff;border-right:1px solid ${S_LINE}}`,
  `.yqb-sales .yqb-mark{left:14px;top:16px;width:36px;height:36px;border-radius:12px;background:${S_ACCENT}}`,
  `.yqb-sales .yqb-nav{left:10px;right:10px;top:76px;display:flex;flex-direction:column;gap:4px}`,
  `.yqb-sales .yqb-nav>i{position:relative;height:40px;border-radius:12px}`,
  `.yqb-sales .yqb-nav>i:first-child{background:${S_ACCENT}}`,
  `.yqb-sales .yqb-nav>i::before{content:"";position:absolute;left:13px;top:11px;width:18px;height:18px;border-radius:6px;background:${S_BAR}}`,
  `.yqb-sales .yqb-nav>i::after{content:"";display:none;position:absolute;left:44px;top:16px;width:48%;height:8px;border-radius:4px;background:${S_BAR}}`,
  `@media(min-width:768px){.yqb-sales .yqb-tabs{display:none}}`,
  `@media(min-width:1024px){.yqb-sales .yqb-sh{display:none}.yqb-sales .yqb-rail{display:block}.yqb-sales .yqb-sp{left:64px;top:0;padding:32px 40px}}`,
  `@media(min-width:1280px){html:not([data-boot-rail=narrow]) .yqb-sales .yqb-rail{width:240px}html:not([data-boot-rail=narrow]) .yqb-sales .yqb-sp{left:240px}html:not([data-boot-rail=narrow]) .yqb-sales .yqb-nav>i::after{display:block}}`,
].join('')

/** A part of a frame: its class and its own parts, painted as <i class="…"> (aria-hidden as a whole). */
export type BootPart = readonly [string, (readonly BootPart[])?]

const many = (n: number): BootPart[] => Array.from({ length: n }, () => [''] as const)

/** The frames' parts — the served node and React's copies are made from this one list. */
export const BOOT_PARTS: Record<BootKind, readonly BootPart[]> = {
  night: [['yqb-dome'], ['yqb-glow'], ['yqb-veil']],
  plain: [],
  shell: [
    ['yqb-side', [['yqb-mark'], ['yqb-name'], ['yqb-nav', many(9)]]],
    ['yqb-top', [['yqb-btn'], ['yqb-title'], ['yqb-chip'], ['yqb-av']]],
    ['yqb-page', [['yqb-h'], ['yqb-sub'], ['yqb-tiles', many(4)], ['yqb-card']]],
  ],
  sales: [
    ['yqb-sh', [['yqb-av'], ['yqb-t1'], ['yqb-t2'], ['yqb-bell']]],
    ['yqb-rail', [['yqb-mark'], ['yqb-nav', many(6)]]],
    ['yqb-sp', [['yqb-search'], ['yqb-chips', many(3)], ['yqb-row'], ['yqb-row'], ['yqb-row'], ['yqb-row'], ['yqb-row'], ['yqb-row']]],
    ['yqb-tabs', many(5)],
  ],
}

const KINDS: readonly BootKind[] = ['night', 'plain', 'shell', 'sales']

function partsHtml(parts: readonly BootPart[]): string {
  return parts.map(([cls, kids]) => `<i${cls ? ` class="${cls}"` : ''}>${kids ? partsHtml(kids) : ''}</i>`).join('')
}

/** The served node: every frame, of which html[data-boot] shows one. No text, no images, no requests. */
export function bootHtml(): string {
  return `<div id="${BOOT_ID}" aria-hidden="true">${KINDS.map((k) => `<div class="yqb yqb-${k}">${partsHtml(BOOT_PARTS[k])}</div>`).join('')}</div>`
}
