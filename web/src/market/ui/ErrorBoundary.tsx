import { Component, type ReactNode } from 'react'
import { errorMeta } from '@/lib/errorMeta'
import { track } from '../lib/events'
import { routeName } from '../lib/vitals'
import { S } from '../strings'

interface State {
  error: Error | null
}

interface Props {
  children: ReactNode
  /** one page broke: an in-shell card (shell/RouteOutlet) */
  page?: boolean
  /** an overlay broke (the product panel, the palette): a small card over the page, with Reload and,
   *  given `onDismiss`, Close — the shell and the page under it stay */
  overlay?: boolean
  onDismiss?: () => void
  /** a part the page can do without (a home section, the opening): nothing at all */
  silent?: boolean
  /** after the error is reported (e.g. lift a boot frame the broken part was meant to remove) */
  onError?: () => void
}

/** Last line of defence for the merchant: a calm card and a reload, in market tokens. The error
 *  itself goes out as a shop_events `error` row (build, class, scrubbed message, route template)
 *  so a broken release is seen in Shop Analytics before a merchant reports it (R6). Since the final
 *  review (28-Oct-2026) the layers below the app's own boundary are per page, per overlay and per
 *  optional section, so one part that cannot load never replaces the whole shop. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('[YQ] Unhandled error:', error)
    try {
      track('error', { meta: { ...errorMeta('market', error, routeName(window.location.pathname)) } })
    } catch {
      /* telemetry never throws */
    }
    this.props.onError?.()
  }

  render() {
    if (this.state.error && this.props.silent) return null
    if (this.state.error && this.props.overlay) {
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false
      return (
        <div role="alert" className="fixed inset-x-3 bottom-[calc(var(--m-bottom-stack,0px)+var(--m-safe-b,0px)+12px)] z-sheet mx-auto max-w-sm rounded-lg border border-line bg-surface p-4 shadow-3 md:bottom-6">
          <p className="font-display text-md font-bold text-ink">{offline ? S.states.couldNot : S.states.errorTitle}</p>
          <p className="mt-1 text-sm text-ink-2">{offline ? S.states.couldNotHint : S.states.errorHint}</p>
          <div className="mt-3 flex gap-2">
            <button type="button" onClick={() => window.location.reload()} className="h-11 rounded-sm bg-plum px-4 text-sm font-semibold text-white hover:bg-plum-deep">
              {S.states.reload}
            </button>
            {this.props.onDismiss && (
              <button type="button" onClick={this.props.onDismiss} className="h-11 rounded-sm border border-line bg-surface px-4 text-sm font-semibold text-ink hover:bg-plum-wash">
                {S.states.close}
              </button>
            )}
          </div>
        </div>
      )
    }
    if (this.state.error && this.props.page) {
      // one page broke: the shell, the nav and the cart stay; the merchant can go home or reload
      // (a crash used to replace the whole app with the reload card). Offline, the page's code simply
      // could not be fetched: say that, not "something went wrong".
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false
      return (
        <div className="grid min-h-[55vh] place-items-center px-4 py-10">
          <div className="max-w-sm text-center">
            <h1 className="font-display text-xl font-bold text-ink">{offline ? S.states.couldNot : S.states.errorTitle}</h1>
            <p className="mt-2 text-sm text-ink-2">{offline ? S.states.couldNotHint : S.states.errorHint}</p>
            <div className="mt-5 flex justify-center gap-2">
              <button type="button" onClick={() => window.location.reload()} className="h-11 rounded-sm bg-plum px-5 text-sm font-semibold text-white hover:bg-plum-deep">
                {S.states.reload}
              </button>
              <a href="/" className="inline-flex h-11 items-center rounded-sm border border-line bg-surface px-5 text-sm font-semibold text-ink hover:bg-plum-wash">
                {S.nav.home}
              </a>
            </div>
          </div>
        </div>
      )
    }
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center bg-canvas px-4">
          <div className="max-w-sm text-center">
            <img src="/yq-logo-160.webp" alt={S.brand} width={56} height={56} className="mx-auto h-14 w-14 rounded-md" />
            <h1 className="mt-4 font-display text-xl font-bold text-ink">{S.states.errorTitle}</h1>
            <p className="mt-2 text-sm text-ink-2">{S.states.errorHint}</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-5 h-11 rounded-sm bg-plum px-5 text-sm font-semibold text-white hover:bg-plum-deep"
            >
              {S.states.reload}
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
