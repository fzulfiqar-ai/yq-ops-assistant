import { useEffect, useRef, useState } from 'react'
import { ShopApiError, type CartLine, type Quote, type QuoteRequest } from '@/lib/shopApi'

/**
 * Live server price for the current cart.
 *
 * The server is the only thing allowed to price a cart (docs/SHOP.md), so every
 * change re-quotes — debounced 400ms so a customer holding "+" doesn't fire ten
 * requests, and the previous request is aborted so answers can't land out of order.
 * The last good quote stays on screen while a new one is in flight: the totals must
 * never flash to zero just because the network is slow.
 *
 * `quoting` is derived (the answer on screen is for an older cart), so the hook
 * never has to set state synchronously inside its effect.
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
}

interface Answer {
  sig: string
  quote: Quote | null
  error: string
}

const NETWORK_ERROR = 'Could not reach the price server. Your cart is safe — please try again.'

export function useQuote(
  enabled: boolean,
  lines: CartLine[],
  couponCode: string,
  referralCode: string,
  fetchQuote: QuoteFetcher,
): QuoteState {
  const [answer, setAnswer] = useState<Answer>({ sig: '', quote: null, error: '' })
  const abortRef = useRef<AbortController | null>(null)

  // Serialise the inputs so the effect only re-runs on a real change, not on
  // every render that happened to rebuild the same array.
  const signature = JSON.stringify({ lines, couponCode, referralCode })
  const idle = !enabled || lines.length === 0

  useEffect(() => {
    if (idle) {
      abortRef.current?.abort()
      return
    }
    const { lines: l, couponCode: c, referralCode: r } = JSON.parse(signature) as {
      lines: CartLine[]
      couponCode: string
      referralCode: string
    }
    const timer = setTimeout(() => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      fetchQuote({ lines: l, coupon_code: c || '', referral_code: r || '' }, ctrl.signal)
        .then((q) => {
          if (!ctrl.signal.aborted) setAnswer({ sig: signature, quote: q, error: '' })
        })
        .catch((e: unknown) => {
          if (ctrl.signal.aborted) return
          const msg = e instanceof ShopApiError ? e.detail || e.message : NETWORK_ERROR
          // Keep the last good quote visible — only the error line is new.
          setAnswer((prev) => ({ sig: signature, quote: prev.quote, error: msg }))
        })
    }, 400)
    return () => clearTimeout(timer)
  }, [signature, idle, fetchQuote])

  useEffect(() => () => abortRef.current?.abort(), [])

  if (idle) return { quote: null, quoting: false, error: '' }
  return { quote: answer.quote, quoting: answer.sig !== signature, error: answer.error }
}
