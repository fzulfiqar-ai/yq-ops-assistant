/** @type {import('tailwindcss').Config} */
// The portal's theme on the tokens in src/index.css (ship 2: the logo plum, Instrument Sans + Sora — one brand
// with the marketplace's tailwind.market.config.js). Every colour takes an alpha (`bg-primary/10`).
const c = (v) => `hsl(var(${v}) / <alpha-value>)`

export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        border: c('--border'),
        input: c('--input'),
        ring: c('--ring'),
        background: c('--background'),
        foreground: c('--foreground'),
        primary: { DEFAULT: c('--primary'), foreground: c('--primary-foreground'), deep: c('--primary-deep') },
        sash: c('--sash'),
        secondary: { DEFAULT: c('--secondary'), foreground: c('--secondary-foreground') },
        muted: { DEFAULT: c('--muted'), foreground: c('--muted-foreground') },
        accent: { DEFAULT: c('--accent'), foreground: c('--accent-foreground') },
        card: { DEFAULT: c('--card'), foreground: c('--card-foreground') },
        popover: { DEFAULT: c('--popover'), foreground: c('--popover-foreground') },
        destructive: { DEFAULT: c('--destructive'), foreground: c('--destructive-foreground') },
        success: { DEFAULT: c('--success'), foreground: c('--success-foreground') },
        warning: { DEFAULT: c('--warning'), foreground: c('--warning-foreground') },
        info: { DEFAULT: c('--info'), foreground: c('--info-foreground') },
        target: c('--target'),
        night: { DEFAULT: c('--night'), 2: c('--night-2') },
        wa: c('--wa'),
      },
      // one scale that climbs (sm < md < lg < xl < 2xl); 2xl used to fall back to Tailwind's 16 px, under xl's 17.6
      borderRadius: {
        '2xl': 'calc(var(--radius) + 8px)',
        xl: 'calc(var(--radius) + 4px)',
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['Instrument Sans', 'Instrument Sans Fallback', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        display: ['Sora', 'Sora Fallback', 'Instrument Sans', 'system-ui', 'sans-serif'],
      },
      // the type scale's named steps beside Tailwind's: 2xs for footnotes and labels, kpi for a tile's figure
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        kpi: ['1.625rem', { lineHeight: '1.15', letterSpacing: '-0.02em' }],
      },
      boxShadow: {
        // ink-tinted depth; a plum tint only where something lifts (never the old violet glow)
        soft: '0 1px 2px rgba(26,20,40,.04), 0 8px 24px -10px rgba(26,20,40,.10)',
        lift: '0 8px 30px -10px rgba(109,64,145,.30)',
        luxe: '0 1px 2px rgba(26,20,40,.05), 0 18px 40px -16px rgba(109,64,145,.22)',
      },
      keyframes: {
        float: { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-6px)' } },
      },
      animation: {
        float: 'float 5s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
