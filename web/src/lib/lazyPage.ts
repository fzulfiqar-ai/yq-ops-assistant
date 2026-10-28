import { createElement, lazy, useState, type ComponentType, type LazyExoticComponent, type ReactElement } from 'react'

/**
 * A lazy component that can be fetched ahead and renders WITHOUT suspending once its code is here
 * (28-Oct-2026, final review). Two things React.lazy alone gets wrong for a shop:
 *
 *  - It suspends on its first render even when the module was already fetched, and a Suspense
 *    boundary that shows its fallback holds it for at least 300 ms — so a page the shell had warmed
 *    still flashed its skeleton on every first visit. Here, a component whose code has arrived
 *    renders the module directly (decided once per mount, so a mounted page never swaps type).
 *  - It keeps a rejected import for the life of the tab: one dropped signal and that page or panel
 *    stayed broken until a reload. Here, the next mount after a failure asks again.
 *
 * A failed fetch reaches the nearest error boundary exactly once: the lazy resolves to a component
 * that throws the error and only then re-arms for the next mount. Re-arming any earlier loops — a
 * component that suspends while mounting is mounted afresh on the retry, picks the new lazy, fetches,
 * fails, retries… and never shows the error (the panel chunk was requested over and over).
 *
 * `preload()` fetches the code (idle warm-ups, a hover); it never throws.
 */
export type Preloadable<P> = ((props: P) => ReactElement) & { preload: () => Promise<void> }

type Module<P> = { default: ComponentType<P> }

export function lazyPage<P extends object>(load: () => Promise<Module<P>>): Preloadable<P> {
  let loaded: ComponentType<P> | null = null
  let pending: Promise<Module<P>> | null = null
  let Lazy: LazyExoticComponent<ComponentType<P>>
  const rearm = () => {
    Lazy = lazy(fetchCode)
  }

  function fetchCode(): Promise<Module<P>> {
    if (!pending) {
      const attempt: Promise<Module<P>> = load()
        .then((mod) => {
          // a deploy's missing chunk that the reload rule took over resolves to nothing
          if (!mod || !mod.default) throw new Error('The page code did not load')
          loaded = mod.default
          return mod
        })
        .catch((err: unknown) => {
          if (pending === attempt) pending = null // whoever asks next fetches again
          return { default: failedImport<P>(err, rearm) }
        })
      pending = attempt
    }
    return pending
  }
  Lazy = lazy(fetchCode)

  function Page(props: P): ReactElement {
    const [Component] = useState<ComponentType<P> | LazyExoticComponent<ComponentType<P>>>(() => loaded ?? Lazy)
    return createElement(Component as ComponentType<P>, props)
  }
  return Object.assign(Page, { preload: () => fetchCode().then(() => undefined) })
}

/** Renders the failed import as an error for the nearest boundary — and re-arms the page's lazy as it
 *  does, so the mount after this one (another product, a Try again) fetches the code again. */
function failedImport<P>(err: unknown, rearm: () => void): ComponentType<P> {
  return function FailedImport(): never {
    rearm()
    throw err
  }
}
