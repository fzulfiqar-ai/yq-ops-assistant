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
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
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
