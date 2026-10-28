import { useCallback, useEffect, useRef, useState } from 'react'
import { ShopApiError, type CartLine, type Quote, type QuoteRequest } from '@/lib/shopApi'
import { NO_ANSWER, quoteView, type QuoteAnswer } from './checkout'

/**
 * Live server price for the current cart.
 *
 * The server is the only thing allowed to price a cart (docs/SHOP.md), so every
 * change re-quotes — debounced 400ms so a customer holding "+" doesn't fire ten
 * requests, and the previous request is aborted so answers can't land out of order.
 * The last good quote stays on screen while a new one is in flight: the totals must
 * never flash to zero just because the network is slow.
 *
 * `quoting`, `error` and `fresh` are derived (checkout.quoteView: the answer on screen is
 * for an older request or an older cart), so the hook never has to set state synchronously
 * inside its effect. An error belongs to the request it answered: a new cart or a Retry
 * clears it while that check is on its way. `retry` asks again at once (no 400 ms wait).
 * An emptied cart forgets its price, so the next first line starts from "checking", not
 * from the price of a cart that no longer exists.
 *
 * The transport is injected: the public catalog quotes through the token endpoint,
 * a logged-in salesman through the bearer one. Either way an answer whose
 * controller was already aborted is dropped, so the "newest wins" rule holds even
 * when the underlying call cannot itself be cancelled.
 */

export type QuoteFetcher = (body: QuoteRequest, signal?: AbortSignal) => Promise<Quote>

export interface QuoteState {
  quote: Quote | null
  quoting: boolean
  error: string
  /** `quote` priced the cart on screen (else it is the last good price, for an older cart) */
  fresh: boolean
  /** ask the server again now — the Retry after a failed check */
  retry: () => void
}

const NETWORK_ERROR = 'Could not reach the price server. Your cart is safe — please try again.'

export function useQuote(
  enabled: boolean,
  lines: CartLine[],
  couponCode: string,
  referralCode: string,
  fetchQuote: QuoteFetcher,
): QuoteState {
  const [answer, setAnswer] = useState<QuoteAnswer>(NO_ANSWER)
  const [attempt, setAttempt] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  // a Retry asks at once; any other change waits the 400 ms
  const nowRef = useRef(false)

  // Serialise the inputs so the effect only re-runs on a real change, not on
  // every render that happened to rebuild the same array. `req` is one request:
  // the cart, and the attempt (a Retry is a new request for the same cart).
  const cart = JSON.stringify({ lines, couponCode, referralCode })
  const req = `${attempt}|${cart}`
  const idle = !enabled || lines.length === 0
  if (idle && answer !== NO_ANSWER) setAnswer(NO_ANSWER)

  useEffect(() => {
    if (idle) {
      abortRef.current?.abort()
      return
    }
    const { lines: l, couponCode: c, referralCode: r } = JSON.parse(cart) as {
      lines: CartLine[]
      couponCode: string
      referralCode: string
    }
    const wait = nowRef.current ? 0 : 400
    nowRef.current = false
    const timer = setTimeout(() => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      fetchQuote({ lines: l, coupon_code: c || '', referral_code: r || '' }, ctrl.signal)
        .then((q) => {
          if (!ctrl.signal.aborted) setAnswer({ req, cart, quote: q, error: '' })
        })
        .catch((e: unknown) => {
          if (ctrl.signal.aborted) return
          const msg = e instanceof ShopApiError ? e.detail || e.message : NETWORK_ERROR
          // Keep the last good quote visible — only the error line is new.
          setAnswer((prev) => ({ req, cart: prev.cart, quote: prev.quote, error: msg }))
        })
    }, wait)
    return () => clearTimeout(timer)
  }, [req, cart, idle, fetchQuote])

  useEffect(() => () => abortRef.current?.abort(), [])

  const retry = useCallback(() => {
    nowRef.current = true
    setAttempt((n) => n + 1)
  }, [])

  if (idle) return { quote: null, quoting: false, error: '', fresh: false, retry }
  return { ...quoteView(answer, cart, req), retry }
}
