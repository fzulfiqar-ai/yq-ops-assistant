import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { ShopApiError } from '@/lib/shopApi'
import { currentRef, deviceId, rememberedOrders } from '../lib/device'
import { searchItems } from '../lib/search'
import { useMarket } from '../MarketContext'
import { locale } from '../strings'
import { pageTemplate, postAsk, postFeedback, type AskHistory, type FeedbackKind } from './assistApi'
import { load, MAX_MSGS, newId, save, type ErrorKind, type Msg, type Saved } from './store'

/** The chat's state machine: messages, the call, the fallback search, feedback, persistence. */

export const MAX_LEN = 500

function errorKind(e: unknown): ErrorKind {
  if (e instanceof ShopApiError) {
    if (e.status === 404) return 'off'
    if (e.status === 429) return 'slow'
    return 'busy'
  }
  return 'net'
}

export function useAssistChat(itemCodeProp: string | null) {
  const { ensureIndex, items, itemsByCode } = useMarket()
  const { pathname } = useLocation()
  const [state, setState] = useState<Saved>(load)
  const [busy, setBusy] = useState(false)
  const stateRef = useRef(state)
  const abortRef = useRef<AbortController | null>(null)
  const pathRef = useRef(pathname)
  useEffect(() => {
    pathRef.current = pathname
  }, [pathname])

  const update = useCallback((fn: (s: Saved) => Saved) => {
    const next = fn(stateRef.current)
    stateRef.current = next
    setState(next)
    save(next)
  }, [])

  // a product asked for the chat: remember it as this conversation's subject
  useEffect(() => {
    if (itemCodeProp && itemCodeProp !== stateRef.current.itemCode) update((s) => ({ ...s, itemCode: itemCodeProp }))
  }, [itemCodeProp, update])

  useEffect(() => () => abortRef.current?.abort(), [])

  const patch = useCallback(
    (id: string, p: Partial<Msg>) => update((s) => ({ ...s, msgs: s.msgs.map((m) => (m.id === id ? { ...m, ...p } : m)) })),
    [update],
  )

  const send = useCallback(
    async (raw: string, retryOf?: string) => {
      const text = raw.trim().slice(0, MAX_LEN)
      if (!text || abortRef.current) return
      const prior = stateRef.current.msgs.filter((m) => !m.error && m.id !== retryOf)
      const history: AskHistory[] = prior.slice(-6).map((m) => ({ role: m.role, text: m.text.slice(0, MAX_LEN) }))
      update((s) => {
        const base = retryOf ? s.msgs.filter((m) => m.id !== retryOf) : [...s.msgs, { id: newId(), role: 'user' as const, text }]
        return { ...s, msgs: base.slice(-MAX_MSGS) }
      })
      const ctrl = new AbortController()
      abortRef.current = ctrl
      setBusy(true)
      try {
        const res = await postAsk(
          {
            message: text,
            history,
            lang: locale.lang,
            device_id: deviceId(),
            session_id: stateRef.current.sid,
            item_code: stateRef.current.itemCode,
            order_tokens: rememberedOrders()
              .map((o) => o.token)
              .slice(0, 20),
            referral_code: currentRef(),
          },
          ctrl.signal,
        )
        if (ctrl.signal.aborted) return
        let searchCodes: string[] | undefined
        let miss = Boolean(res.miss)
        const known = (res.items || []).filter((c) => itemsByCode.has(c))
        if (res.fallback) {
          const index = await ensureIndex().catch(() => null)
          searchCodes = index ? searchItems(index, items, text, 6).map((i) => i.item_code) : []
          if (!searchCodes.length) miss = true
        }
        if (ctrl.signal.aborted) return
        update((s) => ({
          ...s,
          msgs: [
            ...s.msgs,
            {
              id: newId(),
              role: 'assistant' as const,
              text: res.reply || '',
              question: text,
              intent: res.intent,
              items: known.slice(0, 6),
              add: (res.add || []).filter((l) => l && l.item_code && l.qty > 0),
              orders: res.orders || [],
              handoff: res.handoff || null,
              searchCodes,
              fallback: Boolean(res.fallback),
              miss,
              turnId: res.turn_id ?? null,
            },
          ].slice(-MAX_MSGS),
        }))
      } catch (e) {
        if (ctrl.signal.aborted) return
        const kind = errorKind(e)
        update((s) => ({
          ...s,
          msgs: [...s.msgs, { id: newId(), role: 'assistant' as const, text: '', error: kind, retryText: text, question: text }].slice(-MAX_MSGS),
        }))
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null
        setBusy(false)
      }
    },
    [ensureIndex, items, itemsByCode, update],
  )

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    update(() => ({ sid: newId(), msgs: [], itemCode: null }))
  }, [update])

  /** Fire-and-forget feedback; resolves false when it could not be sent. */
  const feedback = useCallback(async (kind: FeedbackKind, text: string | undefined, turnId: number | null): Promise<boolean> => {
    try {
      await postFeedback({
        kind,
        text: text ? text.trim().slice(0, 1000) : undefined,
        turn_id: turnId,
        device_id: deviceId(),
        session_id: stateRef.current.sid,
        lang: locale.lang,
        page: pageTemplate(pathRef.current),
      })
      return true
    } catch {
      return false
    }
  }, [])

  const vote = useCallback(
    (m: Msg, v: 'up' | 'down') => {
      if (m.vote) return
      patch(m.id, { vote: v })
      void feedback(v, undefined, m.turnId ?? null)
    },
    [feedback, patch],
  )

  return { msgs: state.msgs, itemCode: state.itemCode, busy, send, reset, patch, feedback, vote }
}
