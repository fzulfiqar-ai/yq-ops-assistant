import { Component, useLayoutEffect, useRef, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { reportError } from './errorMeta'
import { rearmFailedPages } from './pageCode'
import { glideFrames } from './pageGlide'

/**
 * The frame every signed-in page renders in (ship 2, plan items 13 and 24).
 *
 *  - PageGlide: the page glide. The next page fades up the moment it is there (8 px and opacity,
 *    200 ms, on the enter curve); it never waits for the old page to leave, and never plays on the
 *    shell's first page (the boot frame already did that job). Under reduced motion, or on a weak
 *    phone (html[data-lite]), an opacity fade of 120 ms — calm, but alive. Web Animations only:
 *    nothing is left on the element afterwards, and the CSS reduced-motion rule cannot flatten it.
 *  - PageSkeleton: the one page-shaped skeleton shown while a page's code is still downloading (it
 *    replaced a spinner in the middle of an empty page): a title, a row of tiles, a card.
 *  - PageErrorBoundary: a page that breaks shows its own error with Reload; the menu, the top bar and
 *    every other page keep working. The app-wide screen (components/ErrorBoundary) is left for a
 *    broken shell.
 */
export function PageGlide({ glide, className, children }: { glide: boolean; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!glide || !el || typeof el.animate !== 'function') return
    // Started in the next frame's callbacks — still before that frame paints, so the page never shows
    // without its first keyframe — and not here: animate() makes the browser compute the fresh page's
    // style at once, in the middle of React's commit, and again after the commit's own updates.
    let run: Animation | null = null
    const raf = requestAnimationFrame(() => {
      const { keyframes, options } = glideFrames(calmMotion())
      run = el.animate(keyframes, options)
    })
    return () => {
      cancelAnimationFrame(raf)
      run?.cancel()
    }
  }, [glide])
  return (
    <div ref={ref} data-page-frame="" className={className}>
      {children}
    </div>
  )
}

function calmMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.hasAttribute('data-lite')
  } catch {
    return false
  }
}

export function PageSkeleton() {
  return (
    <div data-page-skeleton="" role="status" aria-busy="true" aria-label="Loading the page">
      <div className="mb-6 pl-4">
        <Skeleton className="h-8 w-56 max-w-[70%]" />
        <Skeleton className="mt-2 h-4 w-80 max-w-[85%]" />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[88px] rounded-xl" />)}
      </div>
      <Skeleton className="h-72 rounded-xl" />
    </div>
  )
}

/** a page whose code did not download (a dropped signal, or a deploy since the tab opened) */
function isLoadError(error: Error | null): boolean {
  const m = `${error?.name || ''} ${error?.message || ''}`
  return /dynamically imported module|Importing a module script failed|page code did not load|ChunkLoadError|Loading chunk/i.test(m)
}

interface BoundaryProps {
  children: ReactNode
  /** the page's path: a new page starts without the last page's error */
  resetKey: string
}

interface BoundaryState {
  error: Error | null
  key: string
}

export class PageErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, key: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<BoundaryState> {
    return { error }
  }

  static getDerivedStateFromProps(props: BoundaryProps, state: BoundaryState): Partial<BoundaryState> | null {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null
  }

  componentDidCatch(error: Error) {
    console.error('[YQ] A page failed:', error)
    reportError('portal', error)
    // the error is on screen now: a page whose code did not download asks again on its next mount
    // (never before — the retries React makes while it settles the error would fetch in a loop)
    rearmFailedPages()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const load = isLoadError(error)
    return (
      <div role="alert" data-page-error="" className="grid min-h-[50vh] place-items-center px-4 py-10">
        <div className="max-w-sm text-center">
          <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-2xl bg-destructive/10 text-destructive">
            <AlertTriangle size={20} aria-hidden="true" />
          </div>
          <h2 className="font-display text-lg font-bold text-foreground">{load ? 'This page did not load' : 'This page hit an error'}</h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            {load
              ? 'Its code could not be downloaded — the connection may have dropped. The rest of the portal still works.'
              : 'Something on it went wrong. The rest of the portal still works.'}
            {' '}Reloading usually fixes it.
          </p>
          <Button type="button" className="mt-4" onClick={() => window.location.reload()}>
            <RotateCw size={15} aria-hidden="true" /> Reload
          </Button>
        </div>
      </div>
    )
  }
}
