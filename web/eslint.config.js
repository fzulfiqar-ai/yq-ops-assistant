import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // The merchant build has its own primitives (src/market/ui) and stylesheet; portal components
    // carry portal tokens that the market CSS does not define.
    files: ['src/market/**/*.{ts,tsx}', 'src/MarketApp.tsx', 'src/main.market.tsx'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            { group: ['@/components/*', '@/components/ui/*'], message: 'Market code uses src/market/ui primitives, not portal components.' },
            { group: ['@/pages/shop/ProductSheet', '@/pages/shop/ProductImage', '@/pages/shop/Select', '@/pages/shop/shared', '@/pages/shop/ProductCard', '@/pages/shop/CartDrawer'], message: 'Market code uses src/market equivalents.' },
            { group: ['@/lib/api', '@/lib/supabase', '@/lib/auth', '@/lib/theme'], message: 'The merchant bundle must not carry the session client.' },
            { group: ['motion', 'motion/*', 'recharts'], message: 'No animation/chart library in the merchant bundle (CSS/WAAPI only).' },
          ],
        },
      ],
    },
  },
])
