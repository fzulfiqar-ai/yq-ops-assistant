/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        secondary: { DEFAULT: 'hsl(var(--secondary))', foreground: 'hsl(var(--secondary-foreground))' },
        muted: { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        accent: { DEFAULT: 'hsl(var(--accent))', foreground: 'hsl(var(--accent-foreground))' },
        card: { DEFAULT: 'hsl(var(--card))', foreground: 'hsl(var(--card-foreground))' },
        destructive: { DEFAULT: 'hsl(var(--destructive))', foreground: 'hsl(var(--destructive-foreground))' },
        success: { DEFAULT: 'hsl(var(--success))', foreground: 'hsl(var(--success-foreground))' },
        warning: { DEFAULT: 'hsl(var(--warning))', foreground: 'hsl(var(--warning-foreground))' },
      },
      borderRadius: {
        xl: 'calc(var(--radius) + 4px)',
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ["'Space Grotesk'", 'Inter', 'sans-serif'],
      },
      boxShadow: {
        soft: '0 1px 2px rgba(24,16,48,.04), 0 8px 24px -8px rgba(24,16,48,.10)',
        lift: '0 8px 30px -8px rgba(109,40,217,.28)',
        // premium tiers — colored, multi-layer depth for the luxe surfaces
        luxe: '0 1px 0 0 rgba(255,255,255,.6) inset, 0 2px 4px rgba(24,16,48,.04), 0 18px 40px -16px rgba(109,40,217,.20)',
        'luxe-hover': '0 1px 0 0 rgba(255,255,255,.7) inset, 0 4px 8px rgba(24,16,48,.06), 0 28px 56px -18px rgba(109,40,217,.34)',
        glow: '0 0 0 1px rgba(124,58,237,.18), 0 8px 32px -8px rgba(124,58,237,.40)',
      },
      keyframes: {
        'fade-up': { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        float: { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-6px)' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        sheen: { '0%': { transform: 'translateX(-120%) skewX(-12deg)' }, '60%,100%': { transform: 'translateX(220%) skewX(-12deg)' } },
      },
      animation: {
        'fade-up': 'fade-up .5s cubic-bezier(.16,1,.3,1) both',
        float: 'float 5s ease-in-out infinite',
        sheen: 'sheen 1.1s cubic-bezier(.16,1,.3,1)',
      },
    },
  },
  plugins: [],
}
