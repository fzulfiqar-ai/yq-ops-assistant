/**
 * Tailwind for the MARKET build only (vite.config.ts switches to this file when VITE_APP=market).
 * A fresh theme on the market tokens in src/market/market.css — not an `extend` of the portal
 * theme — so the merchant CSS carries none of the portal's palette, shadows or luxe layer.
 * The content glob is the market tree only.
 */
const c = (v) => `hsl(var(${v}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/main.market.tsx',
    './src/MarketApp.tsx',
    './src/market/**/*.{ts,tsx}',
  ],
  future: { hoverOnlyWhenSupported: true },
  // v3 component classes defined in @layer components (market.css) that consumers may build
  // dynamically (`canvas-${slide.canvas}`) — keep them even when no source file spells them out.
  safelist: [
    'horizon',
    'is-rising',
    'is-small',
    'band-sash',
    'slider-track',
    'canvas-lilac',
    'canvas-apricot',
    'canvas-mint',
    'canvas-plum',
    'canvas-night',
    'sweep',
  ],
  theme: {
    screens: { sm: '640px', md: '768px', lg: '1024px', xl: '1280px', '2xl': '1440px', '3xl': '1800px' },
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      white: '#ffffff',
      black: '#000000',
      plum: { DEFAULT: c('--m-plum'), deep: c('--m-plum-deep'), ink: c('--m-plum-ink'), soft: c('--m-plum-soft'), wash: c('--m-plum-wash') },
      ink: { DEFAULT: c('--m-ink'), 2: c('--m-ink-2'), 3: c('--m-ink-3') },
      canvas: c('--m-canvas'),
      surface: { DEFAULT: c('--m-surface'), 2: c('--m-surface-2') },
      line: { DEFAULT: c('--m-line'), 2: c('--m-line-2') },
      ok: { DEFAULT: c('--m-ok'), soft: c('--m-ok-soft') },
      warn: { DEFAULT: c('--m-warn'), soft: c('--m-warn-soft') },
      bad: { DEFAULT: c('--m-bad'), soft: c('--m-bad-soft') },
      wa: c('--m-wa'),
      focus: c('--m-focus'),
      // v3 (wholesale redesign)
      deal: { DEFAULT: c('--m-deal'), soft: c('--m-deal-soft'), ink: c('--m-deal-ink') },
      fresh: { DEFAULT: c('--m-fresh'), soft: c('--m-fresh-soft'), ink: c('--m-fresh-ink') },
      sash: c('--m-sash'),
      tile: { lilac: c('--m-tile-lilac'), apricot: c('--m-tile-apricot'), mint: c('--m-tile-mint') },
      night: { DEFAULT: c('--m-night'), 2: c('--m-night-2') },
      arc: { 1: c('--m-arc-1'), 2: c('--m-arc-2'), 3: c('--m-arc-3') },
    },
    fontFamily: {
      sans: ['var(--m-font-sans)'],
      display: ['var(--m-font-display)'],
    },
    borderRadius: {
      none: '0',
      xs: 'var(--m-r-xs)',
      sm: 'var(--m-r-sm)',
      DEFAULT: 'var(--m-r-sm)',
      md: 'var(--m-r-md)',
      lg: 'var(--m-r-lg)',
      xl: 'var(--m-r-xl)',
      full: '9999px',
    },
    boxShadow: {
      none: 'none',
      1: 'var(--m-shadow-1)',
      2: 'var(--m-shadow-2)',
      3: 'var(--m-shadow-3)',
      nav: 'var(--m-shadow-nav)',
    },
    extend: {
      fontSize: {
        '2xs': ['11px', { lineHeight: '14px' }],
        xs: ['12px', { lineHeight: '16px' }],
        sm: ['13px', { lineHeight: '18px' }],
        base: ['15px', { lineHeight: '22px' }],
        md: ['16px', { lineHeight: '24px' }],
        lg: ['18px', { lineHeight: '24px' }],
        xl: ['22px', { lineHeight: '28px' }],
        '2xl': ['28px', { lineHeight: '32px' }],
        '3xl': ['36px', { lineHeight: '40px' }],
      },
      spacing: {
        gutter: 'var(--m-gutter)',
        nav: 'var(--m-nav-h)',
        header: 'var(--m-header-h)',
        'safe-b': 'var(--m-safe-b)',
        'bottom-stack': 'var(--m-bottom-stack)',
      },
      maxWidth: { content: 'var(--m-content-max)' },
      transitionTimingFunction: { m: 'var(--m-ease)', spring: 'var(--m-ease-spring)' },
      transitionDuration: { 1: '120ms', 2: '200ms', 3: '320ms' },
      zIndex: { header: '30', dock: '40', nav: '41', sheet: '50', toast: '60' },
    },
  },
  plugins: [],
}
