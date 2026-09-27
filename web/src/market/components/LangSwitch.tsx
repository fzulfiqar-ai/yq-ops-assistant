import { cn } from '@/lib/utils'
import { switchLang, type Lang } from '../i18n'
import { track } from '../lib/events'
import { locale, S } from '../strings'

/**
 * EN / عربي. Each language is named in its own script and marked with its own `lang`, so the way
 * back is readable (and read out correctly) by someone who cannot read the page's language. The
 * choice is kept on this phone and the page reloads in it (i18n/index.ts switchLang).
 *
 * - `pill`: one button naming the OTHER language — the desktop header (`tone="light"`) and the
 *   phone's plum brand row (`tone="plum"`).
 * - `segmented`: both languages, the current one pressed — the Language row in My YQ.
 *
 * On an English page «عربي» renders in the system's Arabic face: the page's own Arabic font is
 * only in the Arabic font stack (market.css), so English pages never download it for four letters.
 */

const NAMES: Record<Lang, string> = { en: S.lang.english, ar: S.lang.arabic }

function go(next: Lang, where: string) {
  if (next === locale.lang) return
  track('rail_click', { meta: { rail: 'lang', code: `${where}:${next}` } })
  switchLang(next)
}

export function LangSwitch({ variant = 'pill', tone = 'light', where, className }: { variant?: 'pill' | 'segmented'; tone?: 'light' | 'plum'; /** analytics: which surface */ where: string; className?: string }) {
  const other: Lang = locale.lang === 'ar' ? 'en' : 'ar'

  if (variant === 'segmented') {
    return (
      <span role="group" aria-label={S.lang.label} className={cn('inline-flex rounded-sm border border-line p-0.5 text-xs', className)}>
        {(['en', 'ar'] as const).map((l) => {
          const on = l === locale.lang
          return (
            <button
              key={l}
              type="button"
              lang={l}
              aria-pressed={on}
              onClick={() => go(l, where)}
              className={cn(
                'hit relative inline-flex h-9 min-w-[3.25rem] items-center justify-center rounded-xs px-3 font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                on ? 'bg-plum text-white' : 'text-ink-2 hover:bg-plum-wash hover:text-ink',
              )}
            >
              {NAMES[l]}
            </button>
          )
        })}
      </span>
    )
  }

  return (
    <button
      type="button"
      lang={other}
      onClick={() => go(other, where)}
      // the visible word IS the name: «عربي» / "English" (WCAG 2.5.3 — the accessible name contains it)
      className={cn(
        'hit relative inline-flex h-10 shrink-0 items-center justify-center rounded-sm px-3 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2',
        tone === 'plum' ? 'h-11 rounded-full bg-white/15 px-3.5 text-white ring-1 ring-inset ring-white/25 hover:bg-white/25 focus-visible:ring-white/90' : 'text-ink-2 hover:bg-plum-wash hover:text-ink focus-visible:ring-focus/70',
        className,
      )}
    >
      {NAMES[other]}
    </button>
  )
}
