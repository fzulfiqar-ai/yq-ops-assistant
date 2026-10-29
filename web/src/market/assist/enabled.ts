import { useMarket } from '../MarketContext'

/**
 * Whether YQ Assistant exists for this visitor: only when the catalog settings say so
 * (`settings.ai_enabled === true`). Nothing is rendered otherwise.
 *
 * Dev builds only (`npm run dev:market`): add ?assist=1 once to force it on for the tab, ?assist=0
 * to clear. `import.meta.env.DEV` is a build-time constant, so none of this reaches a production bundle.
 */

const DEV_KEY = 'yq-assist-dev'

export function enabledDevForced(): boolean {
  if (!import.meta.env.DEV) return false
  try {
    const q = new URLSearchParams(window.location.search).get('assist')
    if (q === '1') sessionStorage.setItem(DEV_KEY, '1')
    if (q === '0') sessionStorage.removeItem(DEV_KEY)
    return sessionStorage.getItem(DEV_KEY) === '1'
  } catch {
    return false
  }
}

export function useAssistEnabled(): boolean {
  const { settings } = useMarket()
  return settings.ai_enabled === true || enabledDevForced()
}
