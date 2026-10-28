import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Camera, ChevronRight, ClipboardPaste, ImagePlus, Lightbulb, Mic, Square, MessageCircle, PackageSearch, RotateCcw, Search, Send, ShoppingCart, ThumbsDown, ThumbsUp, Truck, X, Sparkles, Check, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { MarketCard } from '../components/MarketCard'
import { ltrText, moneyText, withCurrency } from '../i18n'
import { fmtDateShort, productName } from '../lib/format'
import { useMarket } from '../MarketContext'
import { useReducedMotion } from '../shell/useViewport'
import { Button, AnchorButton, IconButton } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { enabledDevForced } from './enabled'
import { whatsappWith } from './handoff'
import { ListReview } from './ListReview'
import { canRecord, downscale, photoOf } from './media'
import { A } from './strings'
import { useVoice } from './useVoice'
import type { Msg } from './store'
import { MAX_LEN, useAssistChat } from './useAssistChat'

/**
 * YQ Assistant, the conversation: header, messages, composer. It fills whatever frame holds it
 * (the phone sheet, the desktop card, the /ask page) and knows nothing about that frame.
 */

const CHIPS: { key: keyof typeof A.chips; icon: LucideIcon }[] = [
  { key: 'paste', icon: ClipboardPaste },
  { key: 'find', icon: Search },
  { key: 'fit', icon: PackageSearch },
  { key: 'track', icon: Truck },
  { key: 'usual', icon: ShoppingCart },
  { key: 'rep', icon: MessageCircle },
  { key: 'idea', icon: Lightbulb },
]

/* ───────────────────────── one assistant turn ───────────────────────── */

function Dots() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span key={i} className="h-1.5 w-1.5 rounded-full bg-plum/60 motion-safe:animate-pulse" style={{ animationDelay: `${i * 160}ms` }} />
      ))}
    </span>
  )
}

function Bubble({ mine, children }: { mine?: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        'max-w-[88%] whitespace-pre-wrap break-words px-3.5 py-2.5 text-sm leading-relaxed',
        mine ? 'ms-auto rounded-xl rounded-ee-sm bg-plum text-white' : 'me-auto rounded-xl rounded-es-sm bg-surface-2 text-ink ring-1 ring-line-2',
      )}
    >
      {children}
    </div>
  )
}

function Turn({
  m,
  onRetry,
  onVote,
  onPatch,
  onStock,
}: {
  m: Msg
  onRetry: (m: Msg) => void
  onVote: (m: Msg, v: 'up' | 'down') => void
  onPatch: (id: string, p: Partial<Msg>) => void
  onStock: (text: string, turnId: number | null) => Promise<boolean>
}) {
  const { itemsByCode, addMany, rep } = useMarket()
  const toast = useToast()
  const navigate = useNavigate()
  const [stockFailed, setStockFailed] = useState(false)

  if (m.role === 'user') {
    const pic = m.photo ? photoOf(m.id) : undefined
    return (
      <Bubble mine>
        {pic ? <img src={pic} alt={A.photoAlt} className="mb-1.5 max-h-44 w-auto max-w-full rounded-md" /> : null}
        {m.text}
      </Bubble>
    )
  }

  const wa = rep?.whatsapp_url || null

  if (m.error) {
    const line = m.error === 'photo' ? A.errPhoto : m.error === 'off' ? A.errOff : m.error === 'slow' ? A.errSlow : m.error === 'busy' ? A.errBusy : A.errNet
    return (
      <div className="flex flex-col items-start gap-2">
        <Bubble>{line}</Bubble>
        <div className="flex flex-wrap gap-2">
          {m.error !== 'off' && m.error !== 'photo' && (m.retryText || (m.retryPhoto && photoOf(m.retryPhoto))) ? (
            <Button size="sm" variant="secondary" icon={<RotateCcw size={15} aria-hidden="true" />} onClick={() => onRetry(m)}>
              {A.retry}
            </Button>
          ) : null}
          {m.error === 'off' && wa ? (
            <AnchorButton size="sm" variant="wa" href={whatsappWith(wa, m.question || '', [])} target="_blank" rel="noreferrer" icon={<MessageCircle size={15} aria-hidden="true" />}>
              {A.talkToRep}
            </AnchorButton>
          ) : null}
        </div>
      </div>
    )
  }

  const shown = (m.fallback ? m.searchCodes || [] : m.items || []).map((c) => itemsByCode.get(c)).filter((i): i is ShopItem => Boolean(i))
  const lines = (m.add || []).map((l) => ({ item: itemsByCode.get(l.item_code), qty: l.qty })).filter((l): l is { item: ShopItem; qty: number } => Boolean(l.item))
  const codes = Array.from(new Set([...(m.items || []), ...(m.add || []).map((l) => l.item_code), ...(m.searchCodes || [])]))
  const hasRows = Boolean(m.rows?.length)
  const wantsHandoff = !hasRows && (Boolean(m.handoff) || m.intent === 'handoff' || Boolean(m.miss))
  const waUrl = m.handoff?.whatsapp_url || wa
  const waName = m.handoff?.name || rep?.first_name || rep?.name || null

  const addAll = () => {
    const n = addMany(lines, 'assist')
    onPatch(m.id, { added: true })
    if (n > 0) toast(A.added(n), { kind: 'success', action: { label: A.viewCart, onClick: () => navigate('/cart') } })
    else toast(A.addedNone, 'info')
  }

  return (
    <div className="flex min-w-0 flex-col gap-2.5">
      {m.text ? <Bubble>{m.text}</Bubble> : null}

      {m.fallback ? <p className="px-1 text-xs font-semibold text-ink-2">{shown.length ? A.fallbackLine : A.fallbackNone}</p> : null}

      {shown.length && !hasRows ? (
        <div className="flex flex-col gap-2">
          {shown.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="list" from="assist" />
          ))}
        </div>
      ) : null}

      {hasRows ? <ListReview m={m} onPatch={onPatch} onStock={(t) => onStock(t, m.turnId ?? null)} /> : null}

      {lines.length && !hasRows ? (
        <Button
          full
          variant={m.added ? 'secondary' : 'primary'}
          icon={m.added ? <Check size={16} aria-hidden="true" /> : <ShoppingCart size={16} aria-hidden="true" />}
          onClick={addAll}
          disabled={Boolean(m.added)}
        >
          {m.added ? A.addedBtn : A.addAll(lines.length)}
        </Button>
      ) : null}

      {m.orders?.length ? (
        <div className="overflow-hidden rounded-lg border border-line bg-surface" role="list" aria-label={A.orders}>
          {m.orders.map((o, i) => {
            const when = fmtDateShort(o.created_at)
            return (
              <Link
                key={o.token}
                to={`/o/${encodeURIComponent(o.token)}`}
                role="listitem"
                className={cn('flex min-h-[56px] items-center gap-3 px-3.5 py-2.5 text-start hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70', i > 0 && 'border-t border-line-2')}
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <bdi dir="ltr" className="font-display text-sm font-bold text-ink">
                      {o.order_no}
                    </bdi>
                    <span className="rounded-full bg-plum-soft px-2 py-0.5 text-2xs font-semibold text-plum-ink">{o.status_label}</span>
                  </span>
                  <span className="mt-0.5 block text-xs text-ink-2">
                    {[when, A.orderMeta(o.items, o.units), o.total_bhd != null ? withCurrency(moneyText(o.total_bhd)) : null].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <span className="text-xs font-semibold text-plum">{A.openOrder}</span>
                <ChevronRight size={16} className="text-ink-3 rtl:-scale-x-100" aria-hidden="true" />
              </Link>
            )
          })}
        </div>
      ) : null}

      {m.miss && !hasRows ? (
        <div className="flex flex-wrap gap-2">
          {m.stocked ? (
            <p className="px-1 text-sm font-semibold text-ok">{A.askStockDone}</p>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              icon={<Lightbulb size={15} aria-hidden="true" />}
              onClick={async () => {
                setStockFailed(false)
                const ok = await onStock(m.question || '', m.turnId ?? null)
                if (ok) onPatch(m.id, { stocked: true })
                else setStockFailed(true)
              }}
            >
              {A.askStock}
            </Button>
          )}
          {stockFailed ? <p className="w-full px-1 text-xs text-bad">{A.errFeedback}</p> : null}
        </div>
      ) : null}

      {wantsHandoff ? (
        waUrl ? (
          <AnchorButton
            variant="wa"
            href={whatsappWith(waUrl, m.question || '', codes)}
            target="_blank"
            rel="noreferrer"
            icon={<MessageCircle size={16} aria-hidden="true" />}
            className="self-start"
          >
            {m.miss && !m.handoff?.name ? A.tellRep : waName ? A.talkTo(waName) : A.talkToRep}
          </AnchorButton>
        ) : (
          <p className="px-1 text-xs text-ink-2">{A.repLater}</p>
        )
      ) : null}

      {m.turnId != null || m.text ? (
        <div className="flex items-center gap-1 px-0.5">
          {m.vote ? (
            <span className="px-1 text-xs text-ink-2">{A.thanksVote}</span>
          ) : (
            <>
              <button
                type="button"
                aria-label={A.helpful}
                title={A.helpful}
                onClick={() => onVote(m, 'up')}
                className="hit relative grid h-9 w-9 place-items-center rounded-full text-ink-3 hover:bg-plum-wash hover:text-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
              >
                <ThumbsUp size={16} aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={A.notHelpful}
                title={A.notHelpful}
                onClick={() => onVote(m, 'down')}
                className="hit relative grid h-9 w-9 place-items-center rounded-full text-ink-3 hover:bg-plum-wash hover:text-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
              >
                <ThumbsDown size={16} aria-hidden="true" />
              </button>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

/* ───────────────────────── the idea / problem form ───────────────────────── */

function IdeaForm({ onSend, onBack }: { onSend: (kind: 'idea' | 'problem', text: string) => Promise<boolean>; onBack: () => void }) {
  const [kind, setKind] = useState<'idea' | 'problem'>('idea')
  const [text, setText] = useState('')
  const [state, setState] = useState<'edit' | 'sending' | 'done' | 'error'>('edit')
  const ta = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    ta.current?.focus({ preventScroll: true })
  }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t || state === 'sending') return
    setState('sending')
    setState((await onSend(kind, t)) ? 'done' : 'error')
  }

  if (state === 'done') {
    return (
      <div className="flex flex-col items-start gap-3 p-4">
        <p className="rounded-xl rounded-es-sm bg-surface-2 px-3.5 py-2.5 text-sm text-ink ring-1 ring-line-2">{A.ideaThanks}</p>
        <Button variant="secondary" size="sm" onClick={onBack}>
          {A.ideaBack}
        </Button>
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 p-4">
      <h3 className="font-display text-base font-bold text-ink">{A.ideaTitle}</h3>
      <div role="radiogroup" aria-label={A.ideaTitle} className="flex gap-1 rounded-md bg-plum-soft p-1">
        {(['idea', 'problem'] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={kind === k}
            onClick={() => setKind(k)}
            className={cn(
              'h-10 flex-1 rounded-sm text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
              kind === k ? 'bg-surface text-plum shadow-1' : 'text-ink-2 hover:text-ink',
            )}
          >
            {k === 'idea' ? A.ideaKindIdea : A.ideaKindProblem}
          </button>
        ))}
      </div>
      <textarea
        ref={ta}
        value={text}
        onChange={(e) => setText(e.target.value.slice(0, 1000))}
        rows={5}
        aria-label={kind === 'idea' ? A.ideaPlaceholderIdea : A.ideaPlaceholderProblem}
        placeholder={kind === 'idea' ? A.ideaPlaceholderIdea : A.ideaPlaceholderProblem}
        className="w-full resize-none rounded-md border border-line bg-surface p-3 text-[16px] text-ink placeholder:text-ink-3 focus-visible:border-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40"
      />
      {state === 'error' ? <p className="text-xs text-bad">{A.errFeedback}</p> : null}
      <div className="flex gap-2">
        <Button type="submit" loading={state === 'sending'} disabled={!text.trim()} className="flex-1">
          {A.ideaSend}
        </Button>
        <Button type="button" variant="ghost" onClick={onBack}>
          {A.ideaBack}
        </Button>
      </div>
    </form>
  )
}

/* ───────────────────────── the conversation ───────────────────────── */

export function AssistChat({ itemCode, first, onClose, titleId }: { itemCode: string | null; first?: { id: number; text: string } | null; onClose?: () => void; titleId?: string }) {
  const { msgs, itemCode: subject, busy, send, sendPhoto, retryPhoto, reset, patch, feedback, vote } = useAssistChat(itemCode)
  const { itemsByCode, settings } = useMarket()
  const forced = enabledDevForced()
  const voiceOn = (settings.ai_voice === true || forced) && canRecord()
  const photoOn = settings.ai_photo === true || forced
  const reduced = useReducedMotion()
  const [draft, setDraft] = useState('')
  const [idea, setIdea] = useState(false)
  const [hint, setHint] = useState(false)
  const [menu, setMenu] = useState(false)
  const [photoNote, setPhotoNote] = useState('')
  const camRef = useRef<HTMLInputElement>(null)
  const galRef = useRef<HTMLInputElement>(null)
  const handled = useRef<number | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const inputId = useId()
  const subjectItem = subject ? itemsByCode.get(subject) : undefined

  // always show the newest message (and the typing dots)
  useLayoutEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end', behavior: reduced ? 'auto' : 'smooth' })
  }, [msgs.length, busy, idea, reduced])

  const resize = useCallback(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [])

  const voice = useVoice((text) => {
    setDraft((d) => (d.trim() ? `${d.trim()} ${text}` : text).slice(0, MAX_LEN))
    requestAnimationFrame(() => {
      resize()
      taRef.current?.focus({ preventScroll: true })
    })
  })
  // opened from somewhere with a first message (a search that found nothing): send it once. An
  // unmount before the answer cancels that send (useAssistChat), so the next mount sends it again.
  useEffect(() => {
    if (first && handled.current !== first.id) {
      handled.current = first.id
      send(first.text)
    }
  }, [first, send])
  useEffect(
    () => () => {
      handled.current = null
    },
    [],
  )

  // a click anywhere else closes the photo menu
  useEffect(() => {
    if (!menu) return
    const off = () => setMenu(false)
    document.addEventListener('pointerdown', off)
    return () => document.removeEventListener('pointerdown', off)
  }, [menu])

  const onFile = async (file: File | undefined) => {
    setMenu(false)
    if (!file) return
    setPhotoNote('')
    try {
      sendPhoto(await downscale(file))
    } catch {
      setPhotoNote(A.errPhoto)
    }
  }

  const submit = (text: string) => {
    if (!text.trim() || busy) return
    setDraft('')
    setHint(false)
    requestAnimationFrame(resize)
    send(text)
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit(draft)
    }
  }

  const onChip = (key: keyof typeof A.chips) => {
    if (key === 'idea') setIdea(true)
    else if (key === 'paste') {
      setHint(true)
      taRef.current?.focus()
    }
    else submit(A.chips[key])
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <header className="flex items-start gap-3 border-b border-line-2 px-4 pb-3 pt-3">
        <span aria-hidden="true" className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-plum text-white">
          <Sparkles size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="font-display text-base font-bold leading-tight text-ink">
            {A.title}
          </h2>
          <p className="mt-0.5 text-xs leading-snug text-ink-2">{A.subtitle}</p>
        </div>
        {msgs.length ? (
          <IconButton label={A.newChat} onClick={reset}>
            <RotateCcw size={17} aria-hidden="true" />
          </IconButton>
        ) : null}
        {onClose ? (
          <IconButton label={A.close} onClick={onClose} className="-me-1.5">
            <X size={18} aria-hidden="true" />
          </IconButton>
        ) : null}
      </header>

      {idea ? (
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <IdeaForm onSend={(kind, text) => feedback(kind, text, null)} onBack={() => setIdea(false)} />
        </div>
      ) : (
        <>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3.5 py-4" role="log" aria-live="polite" aria-relevant="additions" aria-label={A.title}>
            {subjectItem ? (
              <p className="mb-3 truncate rounded-full bg-plum-soft px-3 py-1.5 text-xs font-semibold text-plum-ink" {...ltrText}>
                {A.aboutItem}: {productName(subjectItem)}
              </p>
            ) : null}

            {!msgs.length ? (
              <div className="flex flex-col gap-4">
                <div className="me-auto max-w-[92%] rounded-xl rounded-es-sm bg-surface-2 px-3.5 py-2.5 text-sm leading-relaxed text-ink ring-1 ring-line-2">{A.welcome}</div>
                <div className="flex flex-wrap gap-2">
                  {CHIPS.map(({ key, icon: Icon }) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => onChip(key)}
                      className="inline-flex min-h-11 items-center gap-2 rounded-full border border-line bg-surface px-3.5 py-2 text-start text-sm font-semibold text-ink transition-colors hover:border-plum/40 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
                    >
                      <Icon size={15} className="shrink-0 text-plum" aria-hidden="true" />
                      {A.chips[key]}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-3.5">
                {msgs.map((m) => (
                  <Turn
                    key={m.id}
                    m={m}
                    onRetry={(x) => (x.retryPhoto ? retryPhoto(x) : send(x.retryText || '', x.id))}
                    onVote={vote}
                    onPatch={patch}
                    onStock={(text, turnId) => feedback('idea', text, turnId)}
                  />
                ))}
              </div>
            )}

            {busy ? (
              <div className="mt-3.5 inline-flex items-center rounded-xl rounded-es-sm bg-surface-2 px-4 py-3 ring-1 ring-line-2" role="status">
                <span className="sr-only">{A.typing}</span>
                <Dots />
              </div>
            ) : null}
            <div ref={endRef} aria-hidden="true" />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              submit(draft)
            }}
            className="border-t border-line-2 bg-surface px-3 pb-[max(0.625rem,var(--m-safe-b))] pt-2.5"
          >
            {voice.note || photoNote ? (
              <p role="status" className="mb-2 rounded-md bg-bad-soft px-3 py-2 text-xs text-bad">
                {voice.note || photoNote}
              </p>
            ) : null}
            {voice.state !== 'idle' ? (
              <div role="status" className="flex min-h-11 items-center gap-2 rounded-xl border border-plum/30 bg-plum-wash px-3">
                <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full bg-bad motion-safe:animate-pulse" />
                <span className="tnum min-w-0 flex-1 truncate text-sm font-semibold text-plum-ink">
                  {voice.state === 'recording' ? A.listening(`${Math.floor(voice.secs / 60)}:${String(voice.secs % 60).padStart(2, '0')}`) : A.transcribing}
                </span>
                {voice.state === 'recording' ? (
                  <button
                    type="button"
                    onClick={voice.stop}
                    aria-label={A.micStop}
                    title={A.micStop}
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-plum text-white hover:bg-plum-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
                  >
                    <Square size={15} fill="currentColor" aria-hidden="true" />
                  </button>
                ) : null}
                <Button size="md" variant="ghost" onClick={voice.cancel}>
                  {A.cancel}
                </Button>
              </div>
            ) : (
            <div className="flex items-end gap-1.5">
              {photoOn ? (
                <div className="relative shrink-0" onPointerDown={(e) => e.stopPropagation()}>
                  <IconButton label={A.photoLabel} aria-haspopup="menu" aria-expanded={menu} disabled={busy} onClick={() => setMenu((v) => !v)} className="rounded-full border border-line">
                    <Camera size={18} aria-hidden="true" />
                  </IconButton>
                  {menu ? (
                    <div role="menu" className="absolute bottom-full start-0 z-10 mb-2 w-52 overflow-hidden rounded-lg border border-line bg-surface shadow-3">
                      <button type="button" role="menuitem" onClick={() => camRef.current?.click()} className="flex min-h-11 w-full items-center gap-2.5 px-3.5 text-start text-sm font-semibold text-ink hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70">
                        <Camera size={16} className="text-plum" aria-hidden="true" />
                        {A.takePhoto}
                      </button>
                      <button type="button" role="menuitem" onClick={() => galRef.current?.click()} className="flex min-h-11 w-full items-center gap-2.5 border-t border-line-2 px-3.5 text-start text-sm font-semibold text-ink hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70">
                        <ImagePlus size={16} className="text-plum" aria-hidden="true" />
                        {A.choosePhoto}
                      </button>
                    </div>
                  ) : null}
                  <input ref={camRef} type="file" accept="image/*" capture="environment" hidden tabIndex={-1} onChange={(e) => { void onFile(e.target.files?.[0]); e.target.value = '' }} />
                  <input ref={galRef} type="file" accept="image/*" hidden tabIndex={-1} onChange={(e) => { void onFile(e.target.files?.[0]); e.target.value = '' }} />
                </div>
              ) : null}
              {voiceOn ? (
                <IconButton label={A.micLabel} disabled={busy} onClick={() => void voice.start()} className="rounded-full border border-line">
                  <Mic size={18} aria-hidden="true" />
                </IconButton>
              ) : null}
              <label htmlFor={inputId} className="sr-only">
                {A.messageLabel}
              </label>
              <textarea
                id={inputId}
                ref={taRef}
                value={draft}
                rows={1}
                maxLength={MAX_LEN}
                disabled={busy}
                placeholder={hint ? A.pasteHint : A.placeholder}
                onChange={(e) => {
                  setDraft(e.target.value)
                  if (e.target.value) setHint(false)
                  resize()
                }}
                onKeyDown={onKey}
                enterKeyHint="send"
                className="max-h-[120px] min-h-11 flex-1 resize-none rounded-xl border border-line bg-surface px-3.5 py-2.5 text-[16px] leading-snug text-ink placeholder:text-ink-3 focus-visible:border-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40 disabled:opacity-60"
              />
              <button
                type="submit"
                aria-label={A.send}
                title={A.send}
                disabled={busy || !draft.trim()}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-plum text-white shadow-1 transition duration-1 ease-m hover:bg-plum-deep active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:pointer-events-none disabled:opacity-40"
              >
                <Send size={17} className="rtl:-scale-x-100" aria-hidden="true" />
              </button>
            </div>
            )}
            <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-2xs text-ink-3">
              <span>{A.privacy}</span>
              {draft.length >= 400 ? <span className="tnum shrink-0">{A.chars(draft.length, MAX_LEN)}</span> : null}
            </div>
          </form>
        </>
      )}
    </div>
  )
}
