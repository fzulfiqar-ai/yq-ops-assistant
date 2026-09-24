import { Component, type ReactNode } from 'react'
import { reportError } from '@/lib/errorMeta'

interface State {
  error: Error | null
}

/** The portal's (and the public /c /o links') last line of defence. `where` names the door in the
 *  shop_events `error` row the boundary sends (R6): 'portal' by default, 'public' from PublicApp. */
export class ErrorBoundary extends Component<{ children: ReactNode; where?: string }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('[YQ] Unhandled error:', error)
    reportError(this.props.where || 'portal', error)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center bg-background px-4">
          <div className="max-w-sm text-center">
            <img src="/yq-logo.png" alt="YQ" className="mx-auto h-14 w-14 rounded-2xl" />
            <h1 className="mt-4 font-display text-xl font-bold text-foreground">Something went wrong</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              The page hit an unexpected error. Reloading usually fixes it.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="mt-5 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground"
            >
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
