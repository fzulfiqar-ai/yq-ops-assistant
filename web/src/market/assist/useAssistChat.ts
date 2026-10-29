import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { ShopApiError, type ShopItem } from '@/lib/shopApi'
import { currentRef, deviceId, rememberedOrders } from '../lib/device'
import { normalizeQty } from '../lib/format'
import { searchItems } from '../lib/search'
import { useMarket, useMarketSearch } from '../MarketContext'
import { locale } from '../strings'
import { pageTemplate, postAsk, postFeedback, postPhoto, type AskHistory, type AskResponse, type FeedbackKind } from './assistApi'
import { forgetPhotos, keepPhoto, photoOf } from './media'
import { A } from './strings'
import { load, MAX_MSGS, newId, save, type ErrorKind, type Msg, type Row, type Saved } from './store'

/** The chat's state machine: messages, the calls, the fallback search, the list rows, feedback, persistence. */

export const MAX_LEN = 500

function errorKind(e: unknown, photo: boolean): ErrorKind {
  if (e instanceof ShopApiError) {
    if (e.status === 404) return 'off'
    if (e.status === 429) return 'slow'
    if (e.status === 400 && photo) return 'photo'
    return 'busy'
  }
  return 'net'
}

/** The server's lines become editable rows: only codes the catalog really has are kept; quantities follow each item's pack rules. */
function buildRows(res: AskResponse, byCode: Map<string, ShopItem>): Row[] {
  const raised = new Map((res.add || []).map((l) => [l.item_code, l.qty]))
  return (res.lines || []).map((l) => {
    const item = l.item_code ? byCode.get(l.item_code) : undefined
    const code = item && l.item_code ? l.item_code : null
    const wanted = (code ? raised.get(code) : null) ?? l.qty ?? 1
    return {
      text: String(l.text || '').slice(0, 200),
      code,
      qty: item ? normalizeQty(item, wanted) : Math.max(1, Number(l.qty) || 1),
      alts: (l.alternatives || []).filter((c) => byCode.has(c)).slice(0, l.status === 'missing' ? 3 : 2),
      status: code ? l.status : 'missing',
    }
  })
}

interface Run {
  question: string
  call: (signal: AbortSignal, ctx: { sid: string }) => Promise<AskResponse>
  user?: Msg
  /** a failed turn to replace (retry) */
  replace?: string
  retry: { text?: string; photo?: string }
  photo: boolean
}

export function useAssistChat(itemCodeProp: string | null) {
  const { items, itemsByCode } = useMarket()
  const { ensureIndex } = useMarketSearch()
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

  // unmounting cancels the request in flight and frees the slot, so a remount (React's StrictMode
  // does one in development) can send again
  useEffect(
    () => () => {
      abortRef.current?.abort()
      abortRef.current = null
    },
    [],
  )

  const patch = useCallback(
    (id: string, p: Partial<Msg>) => update((s) => ({ ...s, msgs: s.msgs.map((m) => (m.id === id ? { ...m, ...p } : m)) })),
    [update],
  )

  const run = useCallback(
    async (r: Run) => {
      if (abortRef.current) return
      update((s) => {
        let msgs = r.replace ? s.msgs.filter((m) => m.id !== r.replace) : s.msgs
        if (r.user) msgs = [...msgs, r.user]
        return { ...s, msgs: msgs.slice(-MAX_MSGS) }
      })
      const ctrl = new AbortController()
      abortRef.current = ctrl
      setBusy(true)
      try {
        const res = await r.call(ctrl.signal, { sid: stateRef.current.sid })
        if (ctrl.signal.aborted) return
        let searchCodes: string[] | undefined
        let miss = Boolean(res.miss)
        const searched = Boolean(res.fallback) && res.intent !== 'photo'
        const known = (res.items || []).filter((c) => itemsByCode.has(c))
        const rows = buildRows(res, itemsByCode)
        if (searched) {
          const index = await ensureIndex().catch(() => null)
          searchCodes = index ? searchItems(index, items, r.question, 6).map((i) => i.item_code) : []
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
              question: r.question,
              intent: res.intent,
              items: known.slice(0, 6),
              add: (res.add || []).filter((l) => l && l.item_code && l.qty > 0),
              orders: res.orders || [],
              handoff: res.handoff || null,
              rows: rows.length ? rows : undefined,
              searchCodes,
              fallback: searched,
              miss,
              turnId: res.turn_id ?? null,
            },
          ].slice(-MAX_MSGS),
        }))
      } catch (e) {
        if (ctrl.signal.aborted) {
          // a cancelled question leaves no unanswered bubble behind (a resend adds its own)
          const uid = r.user?.id
          if (uid) update((s) => ({ ...s, msgs: s.msgs.filter((m) => m.id !== uid) }))
          return
        }
        const kind = errorKind(e, r.photo)
        update((s) => ({
          ...s,
          msgs: [
            ...s.msgs,
            { id: newId(), role: 'assistant' as const, text: '', error: kind, retryText: r.retry.text, retryPhoto: r.retry.photo, question: r.question },
          ].slice(-MAX_MSGS),
        }))
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null
        setBusy(false)
      }
    },
    [ensureIndex, items, itemsByCode, update],
  )

  const send = useCallback(
    (raw: string, retryOf?: string) => {
      const text = raw.trim().slice(0, MAX_LEN)
      if (!text || abortRef.current) return
      const prior = stateRef.current.msgs.filter((m) => !m.error && m.id !== retryOf)
      const history: AskHistory[] = prior.slice(-6).map((m) => ({ role: m.role, text: m.text.slice(0, MAX_LEN) }))
      void run({
        question: text,
        photo: false,
        replace: retryOf,
        retry: { text },
        user: retryOf ? undefined : { id: newId(), role: 'user', text },
        call: (signal, { sid }) =>
          postAsk(
            {
              message: text,
              history,
              lang: locale.lang,
              device_id: deviceId(),
              session_id: sid,
              item_code: stateRef.current.itemCode,
              order_tokens: rememberedOrders()
                .map((o) => o.token)
                .slice(0, 20),
              referral_code: currentRef(),
            },
            signal,
          ),
      })
    },
    [run],
  )

  /** A photo (already downscaled): kept in memory for the bubble and a retry, a placeholder is what is stored. */
  const sendPhoto = useCallback(
    (dataUrl: string, retry?: { errorId: string; userId: string }) => {
      if (abortRef.current) return
      const userId = retry?.userId || newId()
      keepPhoto(userId, dataUrl)
      void run({
        question: A.photoBubble,
        photo: true,
        replace: retry?.errorId,
        retry: { photo: userId },
        user: retry ? undefined : { id: userId, role: 'user', text: A.photoBubble, photo: true },
        call: (signal, { sid }) => postPhoto({ image: dataUrl, lang: locale.lang, device_id: deviceId(), session_id: sid, referral_code: currentRef() }, signal),
      })
    },
    [run],
  )

  const retryPhoto = useCallback(
    (m: Msg) => {
      const url = m.retryPhoto ? photoOf(m.retryPhoto) : undefined
      if (url && m.retryPhoto) sendPhoto(url, { errorId: m.id, userId: m.retryPhoto })
    },
    [sendPhoto],
  )

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    forgetPhotos()
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

  return { msgs: state.msgs, itemCode: state.itemCode, busy, send, sendPhoto, retryPhoto, reset, patch, feedback, vote }
}
