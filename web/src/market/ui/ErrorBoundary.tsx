import { Component, type ReactNode } from 'react'
import { errorMeta } from '@/lib/errorMeta'
import { track } from '../lib/events'
import { routeName } from '../lib/vitals'
import { S } from '../strings'

interface State {
  error: Error | null
}

/** Last line of defence for the merchant: a calm card and a reload, in market tokens. The error
 *  itself goes out as a shop_events `error` row (build, class, scrubbed message, route template)
 *  so a broken release is seen in Shop Analytics before a merchant reports it (R6). */
export class ErrorBoundary extends Component<{ children: ReactNode; page?: boolean }, State> {
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
  }

  render() {
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
