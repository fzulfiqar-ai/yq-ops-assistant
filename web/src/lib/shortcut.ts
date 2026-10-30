/**
 * The search shortcut as this keyboard writes it: ⌘K on a Mac (or an iPad with a keyboard), Ctrl K everywhere
 * else — the office runs on Windows, where the top bar used to say ⌘K. CommandPalette listens for both.
 */
export function shortcutHint(): string {
  if (typeof navigator === 'undefined') return 'Ctrl K'
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } }
  const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || ''
  return /Mac|iPhone|iPad|iPod/i.test(platform) ? '⌘K' : 'Ctrl K'
}
