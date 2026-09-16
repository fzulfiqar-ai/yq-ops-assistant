import { Component, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** Last line of defence for the merchant: a calm card and a reload, in market tokens. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('[YQ] Unhandled error:', error)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center bg-canvas px-4">
          <div className="max-w-sm text-center">
            <img src="/yq-logo-160.webp" alt="YQ" width={56} height={56} className="mx-auto h-14 w-14 rounded-md" />
            <h1 className="mt-4 font-display text-xl font-bold text-ink">Something went wrong</h1>
            <p className="mt-2 text-sm text-ink-2">The page hit an unexpected error. Reloading usually fixes it.</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-5 h-11 rounded-sm bg-plum px-5 text-sm font-semibold text-white hover:bg-plum-deep"
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
