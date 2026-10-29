import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'
import { useLocation } from 'react-router-dom'
import { ShopApiError, type ShopItem } from '@/lib/shopApi'
import { currentRef, deviceId, rememberedOrders } from '../lib/device'
import { normalizeQty } from '../lib/format'
import { searchItems } from '../lib/search'
import { useMarket, useMarketSearch } from '../MarketContext'
import { locale } from '../strings'
import {
  pageTemplate,
  postAsk,
  postAskStream,
  postFeedback,
  postPhoto,
  postTicket,
  type AskAction,
  type AskNext,
  type AskResponse,
  type AskStage,
  type FeedbackKind,
  type TicketBody,
  type TicketReceipt,
} from './assistApi'
import { announceReady } from './open'
import { forgetPhotos, keepPhoto, photoOf } from './media'
import { A } from './strings'
import { load, MAX_MSGS, newId, save, type ErrorKind, type Msg, type Row, type Saved } from './store'

/**
 * The chat's state machine: messages, the calls, the fallback search, the list rows, feedback, persistence.
 *
 * v2 (29-Oct-2026): the conversation lives in this module, not in the panel, so closing the panel no longer
 * cancels a question: the answer lands here and the launcher shows a dot (announceReady). The module is part of
 * the lazily loaded chat chunk, so nothing of it reaches the first download. A turn streams its stages and its
 * cards (postAskStream); the tiles send an `action`; the history the server reads is named by turn ids.
 *
 * 2.1: every answer may carry quick replies (`next`); the chat speaks first with an opening line (`hello`, from
 * data, never a turn), fetched once per session and product.
 */

export const MAX_LEN = 500
const SLOW_MS = 5000

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
      why: l.why,
    }
  })
}

/* ───────────────────────── the session: one conversation per tab, outliving the panel ───────────────────────── */

interface Snap {
  state: Saved
  busy: boolean
  stage: AskStage | null
  slow: boolean
  /** 2.1: the opening line for this session and product (null until it arrives, or when it could not) */
  hello: AskResponse | null
}

const session: { snap: Snap; inflight: AbortController | null; mounted: number; listeners: Set<() => void>; helloKey: string } = {
  snap: { state: load(), busy: false, stage: null, slow: false, hello: null },
  inflight: null,
  mounted: 0,
  listeners: new Set(),
  helloKey: '',
}

function publish(p: Partial<Snap>): void {
  session.snap = { ...session.snap, ...p }
  session.listeners.forEach((l) => l())
}

function update(fn: (s: Saved) => Saved): void {
  const next = fn(session.snap.state)
  save(next)
  publish({ state: next })
}

const subscribe = (l: () => void) => {
  session.listeners.add(l)
  return () => session.listeners.delete(l)
}
const getSnap = () => session.snap

function patchMsg(id: string, p: Partial<Msg>): void {
  update((s) => ({ ...s, msgs: s.msgs.map((m) => (m.id === id ? { ...m, ...p } : m)) }))
}

/** What the catalogue lets the answer show: known codes only, the rows, the fallback search's own cards. */
interface CatalogCtx {
  items: ShopItem[]
  itemsByCode: Map<string, ShopItem>
  ensureIndex: () => Promise<unknown>
}

function answerMsg(res: AskResponse, question: string, ctx: CatalogCtx, extra: Partial<Msg> = {}): Msg {
  const known = (res.items || []).filter((c) => ctx.itemsByCode.has(c))
  const rows = buildRows(res, ctx.itemsByCode)
  return {
    id: newId(),
    role: 'assistant',
    text: res.reply || '',
    question,
    intent: res.intent,
    items: known.slice(0, 9),
    add: (res.add || []).filter((l) => l && l.item_code && l.qty > 0),
    orders: res.orders || [],
    handoff: res.handoff || null,
    rows: rows.length ? rows : undefined,
    turnId: res.turn_id ?? null,
    cards: (res.cards || []).filter((c) => ctx.itemsByCode.has(c.code)),
    alts: res.alts || {},
    plan: res.plan || null,
    ticket: res.ticket || null,
    tickets: res.tickets || [],
    policy: res.policy || null,
    callback: Boolean(res.callback),
    escalated: Boolean(res.escalated),
    miss: Boolean(res.miss),
    next: cleanNext(res.next, ctx),
    ...extra,
  }
}

/** The quick replies the page can act on: a label, then words to send or a known action (a card the catalogue has). */
export function cleanNext(next: AskNext[] | undefined, ctx: Pick<CatalogCtx, 'itemsByCode'>): AskNext[] {
  return (next || [])
    .filter((n) => n && typeof n.label === 'string' && n.label.trim() && (n.say || n.action))
    .filter((n) => n.action !== 'show' || ctx.itemsByCode.has(String(n.q || '')))
    .slice(0, 4)
}

interface Run {
  question: string
  call: (signal: AbortSignal, ctx: { sid: string; onStage: (k: AskStage) => void; onCards: (r: AskResponse) => void }) => Promise<AskResponse>
  user?: Msg
  /** a failed turn to replace (retry) */
  replace?: string
  retry: { text?: string; photo?: string }
  photo: boolean
}

async function run(r: Run, ctx: CatalogCtx): Promise<void> {
  if (session.inflight) return
  update((s) => {
    let msgs = r.replace ? s.msgs.filter((m) => m.id !== r.replace) : s.msgs
    if (r.user) msgs = [...msgs, r.user]
    return { ...s, msgs: msgs.slice(-MAX_MSGS) }
  })
  const ctrl = new AbortController()
  session.inflight = ctrl
  publish({ busy: true, stage: 'reading', slow: false })
  const slowTimer = window.setTimeout(() => publish({ slow: true }), SLOW_MS)
  let provisional: string | null = null
  try {
    const res = await r.call(ctrl.signal, {
      sid: session.snap.state.sid,
      onStage: (k) => publish({ stage: k }),
      onCards: (partial) => {
        // the products are known: show them now, the sentence follows
        if (ctrl.signal.aborted || provisional) return
        const m = answerMsg(partial, r.question, ctx, { pending: true, text: '' })
        provisional = m.id
        update((s) => ({ ...s, msgs: [...s.msgs, m].slice(-MAX_MSGS) }))
      },
    })
    if (ctrl.signal.aborted) return
    let searchCodes: string[] | undefined
    let miss = Boolean(res.miss)
    const searched = Boolean(res.fallback) && res.intent !== 'photo'
    if (searched) {
      const index = (await ctx.ensureIndex().catch(() => null)) as Parameters<typeof searchItems>[0] | null
      searchCodes = index ? searchItems(index, ctx.items, r.question, 6).map((i) => i.item_code) : []
      if (!searchCodes.length) miss = true
    }
    if (ctrl.signal.aborted) return
    const final = answerMsg(res, r.question, ctx, { searchCodes, fallback: searched, miss })
    update((s) => {
      const msgs = provisional ? s.msgs.map((m) => (m.id === provisional ? { ...final, id: provisional } : m)) : [...s.msgs, final]
      return { ...s, msgs: msgs.slice(-MAX_MSGS) }
    })
    if (session.mounted === 0) announceReady()
  } catch (e) {
    if (ctrl.signal.aborted) {
      // a cancelled question leaves no unanswered bubble behind (a resend adds its own)
      const uid = r.user?.id
      update((s) => ({ ...s, msgs: s.msgs.filter((m) => m.id !== uid && m.id !== provisional) }))
      return
    }
    const kind = errorKind(e, r.photo)
    update((s) => ({
      ...s,
      msgs: [
        ...s.msgs.filter((m) => m.id !== provisional),
        { id: newId(), role: 'assistant' as const, text: '', error: kind, retryText: r.retry.text, retryPhoto: r.retry.photo, question: r.question },
      ].slice(-MAX_MSGS),
    }))
    if (session.mounted === 0) announceReady()
  } finally {
    window.clearTimeout(slowTimer)
    if (session.inflight === ctrl) session.inflight = null
    publish({ busy: false, stage: null, slow: false })
  }
}

function askBase(sid: string) {
  return {
    lang: locale.lang,
    device_id: deviceId(),
    session_id: sid,
    item_code: session.snap.state.itemCode,
    order_tokens: rememberedOrders()
      .map((o) => o.token)
      .slice(0, 20),
    referral_code: currentRef(),
    history: [],
  }
}

/** The ids of the last answered turns on screen: the server reads the chat so far from its own log. */
function historyIds(exclude?: string): number[] {
  return session.snap.state.msgs
    .filter((m) => m.role === 'assistant' && !m.error && m.id !== exclude && typeof m.turnId === 'number')
    .slice(-3)
    .map((m) => m.turnId as number)
}

/* ───────────────────────── the hook ───────────────────────── */

export function useAssistChat(itemCodeProp: string | null) {
  const { items, itemsByCode } = useMarket()
  const { ensureIndex } = useMarketSearch()
  const { pathname } = useLocation()
  const snap = useSyncExternalStore(subscribe, getSnap, getSnap)
  const ctxRef = useRef<CatalogCtx>({ items, itemsByCode, ensureIndex })
  const pathRef = useRef(pathname)
  useEffect(() => {
    ctxRef.current = { items, itemsByCode, ensureIndex }
    pathRef.current = pathname
  })

  // the panel is open: an answer that lands now needs no dot on the launcher
  useEffect(() => {
    session.mounted += 1
    return () => {
      session.mounted -= 1
    }
  }, [])

  // a product asked for the chat: remember it as this conversation's subject
  useEffect(() => {
    if (itemCodeProp && itemCodeProp !== session.snap.state.itemCode) update((s) => ({ ...s, itemCode: itemCodeProp }))
  }, [itemCodeProp])

  // 2.1: an empty chat speaks first, once per session and product (from data on the server, never a turn)
  const empty = snap.state.msgs.length === 0
  const subject = snap.state.itemCode
  useEffect(() => {
    if (!empty) return
    const key = `${session.snap.state.sid}|${subject || ''}|${locale.lang}`
    if (session.helloKey === key) return
    session.helloKey = key
    publish({ hello: null })
    postAsk({ ...askBase(session.snap.state.sid), message: 'hello', action: 'hello' })
      .then((res) => {
        if (session.helloKey === key && res && typeof res.reply === 'string' && res.reply) {
          publish({ hello: { ...res, next: cleanNext(res.next, ctxRef.current) } })
        }
      })
      .catch(() => {
        /* the static welcome line stays */
      })
  }, [empty, subject])

  const send = useCallback((raw: string, retryOf?: string) => {
    const text = raw.trim().slice(0, MAX_LEN)
    if (!text || session.inflight) return
    const ids = historyIds(retryOf)
    void run(
      {
        question: text,
        photo: false,
        replace: retryOf,
        retry: { text },
        user: retryOf ? undefined : { id: newId(), role: 'user', text },
        call: (signal, { sid, onStage, onCards }) => postAskStream({ ...askBase(sid), message: text, history_ids: ids }, { onStage, onCards }, signal),
      },
      ctxRef.current,
    )
  }, [])

  /** A tile: its label is the message the merchant sees, its action is what the server answers (no model). */
  const sendAction = useCallback((action: AskAction, label: string, q?: string) => {
    if (session.inflight) return
    void run(
      {
        question: label,
        photo: false,
        retry: { text: label },
        user: { id: newId(), role: 'user', text: label },
        call: (signal, { sid, onStage, onCards }) =>
          postAskStream({ ...askBase(sid), message: label, action, ...(q ? { q: q.slice(0, 60) } : {}), history_ids: historyIds() }, { onStage, onCards }, signal),
      },
      ctxRef.current,
    )
  }, [])

  /** 2.1: a quick reply: words go as the merchant's own message, an action is answered from data. */
  const sendNext = useCallback(
    (n: AskNext) => {
      if (n.action) sendAction(n.action, n.label, n.q)
      else if (n.say) send(n.say)
    },
    [send, sendAction],
  )

  /** A photo (already downscaled): kept in memory for the bubble and a retry, a placeholder is what is stored. */
  const sendPhoto = useCallback((dataUrl: string, retry?: { errorId: string; userId: string }) => {
    if (session.inflight) return
    const userId = retry?.userId || newId()
    keepPhoto(userId, dataUrl)
    void run(
      {
        question: A.photoBubble,
        photo: true,
        replace: retry?.errorId,
        retry: { photo: userId },
        user: retry ? undefined : { id: userId, role: 'user', text: A.photoBubble, photo: true },
        call: (signal, { sid }) => postPhoto({ image: dataUrl, lang: locale.lang, device_id: deviceId(), session_id: sid, referral_code: currentRef() }, signal),
      },
      ctxRef.current,
    )
  }, [])

  const retryPhoto = useCallback(
    (m: Msg) => {
      const url = m.retryPhoto ? photoOf(m.retryPhoto) : undefined
      if (url && m.retryPhoto) sendPhoto(url, { errorId: m.id, userId: m.retryPhoto })
    },
    [sendPhoto],
  )

  const reset = useCallback(() => {
    session.inflight?.abort()
    session.inflight = null
    forgetPhotos()
    publish({ busy: false, stage: null, slow: false, hello: null })
    update(() => ({ sid: newId(), msgs: [], itemCode: null }))
  }, [])

  /** Fire-and-forget feedback; resolves false when it could not be sent. */
  const feedback = useCallback(async (kind: FeedbackKind, text: string | undefined, turnId: number | null): Promise<boolean> => {
    try {
      await postFeedback({
        kind,
        text: text ? text.trim().slice(0, 1000) : undefined,
        turn_id: turnId,
        device_id: deviceId(),
        session_id: session.snap.state.sid,
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
      patchMsg(m.id, { vote: v })
      void feedback(v, undefined, m.turnId ?? null)
    },
    [feedback],
  )

  /** The ticket card's one request; a receipt on the message, or the error's words for the card to show. */
  const submitTicket = useCallback(async (m: Msg, body: Omit<TicketBody, 'device_id' | 'session_id' | 'referral_code' | 'lang' | 'turn_id'>): Promise<TicketReceipt | string> => {
    try {
      const receipt = await postTicket({
        ...body,
        device_id: deviceId(),
        session_id: session.snap.state.sid,
        referral_code: currentRef(),
        lang: locale.lang,
        turn_id: m.turnId ?? null,
      })
      patchMsg(m.id, { receipt })
      return receipt
    } catch (e) {
      return e instanceof ShopApiError && e.detail ? e.detail : A.errFeedback
    }
  }, [])

  return {
    msgs: snap.state.msgs,
    itemCode: snap.state.itemCode,
    busy: snap.busy,
    stage: snap.stage,
    slow: snap.slow,
    hello: snap.hello,
    send,
    sendAction,
    sendNext,
    sendPhoto,
    retryPhoto,
    reset,
    patch: patchMsg,
    feedback,
    vote,
    submitTicket,
  }
}
