import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Camera,
  Check,
  ChevronRight,
  ClipboardList,
  ClipboardPaste,
  Copy,
  ImagePlus,
  Info,
  LifeBuoy,
  Lightbulb,
  Maximize2,
  MessageCircle,
  Mic,
  PackageSearch,
  PhoneCall,
  Repeat,
  RotateCcw,
  Send,
  ShieldCheck,
  ShoppingCart,
  Square,
  ThumbsDown,
  ThumbsUp,
  TrendingUp,
  Truck,
  Volume2,
  X,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { MarketCard } from '../components/MarketCard'
import { locale, ltrText, moneyText, withCurrency } from '../i18n'
import { fmtDateShort, productName } from '../lib/format'
import { useMarket } from '../MarketContext'
import { useReducedMotion } from '../shell/useViewport'
import { AnchorButton, Button, IconButton } from '../ui/Button'
import { useToast } from '../ui/Toast'
import type { AskAction, AskStage } from './assistApi'
import { enabledDevForced } from './enabled'
import { whatsappWith } from './handoff'
import { ListReview } from './ListReview'
import { canRecord, downscale, photoOf } from './media'
import { Orb, type OrbState } from './Orb'
import { A } from './strings'
import { newId, type Msg } from './store'
import { Receipt, TicketCard } from './TicketCard'
import { MAX_LEN, useAssistChat } from './useAssistChat'
import { useVoice } from './useVoice'

/**
 * YQ Assist, the conversation (v2, 29-Oct-2026): header, messages, composer. It fills whatever frame holds it
 * (the phone sheet, the desktop card, the /ask page) and knows nothing about that frame. Corporate, not a toy:
 * replies are clean text beside the orb, never a price in the words (the cards carry the catalogue's own); six
 * tiles answer from data with no model call; a visible stage while it thinks, never a bare spinner; the line
 * under the composer says it is an AI that can make mistakes, and the stock date it shows.
 */

const TILES: { key: keyof typeof A.tiles; action: AskAction; icon: LucideIcon }[] = [
  { key: 'top', action: 'top', icon: TrendingUp },
  { key: 'plan', action: 'plan', icon: ClipboardList },
  { key: 'usual', action: 'usual', icon: Repeat },
  { key: 'orders', action: 'orders', icon: Truck },
  { key: 'ticket', action: 'ticket', icon: LifeBuoy },
  { key: 'handoff', action: 'handoff', icon: MessageCircle },
]
const CHIPS: { key: 'paste' | 'fit' | 'idea'; icon: LucideIcon }[] = [
  { key: 'paste', icon: ClipboardPaste },
  { key: 'fit', icon: PackageSearch },
  { key: 'idea', icon: Lightbulb },
]
const BARS = [0.55, 0.85, 1, 0.7, 0.95, 0.6, 0.8]

type SubmitTicket = Parameters<typeof TicketCard>[0]['onSubmit']

function partOfDay(): 'morning' | 'afternoon' | 'evening' {
  const h = new Date().getHours()
  return h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening'
}

function initials(name: string | null | undefined): string {
  return String(name || 'YQ')
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
}

/* ───────────────────────── pieces of a reply ───────────────────────── */

function Thinking({ stage, slow }: { stage: AskStage | null; slow: boolean }) {
  return (
    <div className="flex gap-2.5" role="status">
      <Orb size={26} state={slow ? 'busy' : 'thinking'} />
      <div className="min-w-0 flex-1">
        <p className="assist-stage pt-0.5 text-sm font-semibold text-plum-ink">{A.stage[stage || 'reading']}</p>
        <div className="mt-2 flex flex-col gap-2" aria-hidden="true">
          <div className="skeleton h-14 rounded-lg" />
          <div className="skeleton h-14 w-4/5 rounded-lg" />
        </div>
        {slow ? <p className="mt-2 text-xs text-ink-2">{A.slowNote}</p> : null}
      </div>
    </div>
  )
}

/** The cards, why each is there when the card cannot say it, a sold-out card followed by what is in stock instead. */
function Cards({ m }: { m: Msg }) {
  const { itemsByCode } = useMarket()
  const codes = m.fallback ? m.searchCodes || [] : m.items || []
  const why = new Map((m.cards || []).map((c) => [c.code, c.why]))
  const alts = m.alts || {}
  const done = new Set<string>()
  const out: ReactNode[] = []
  for (const code of codes) {
    const it = itemsByCode.get(code)
    if (!it || done.has(code)) continue
    done.add(code)
    out.push(
      <div key={code} className="flex flex-col gap-1">
        {why.get(code) === 'usual' ? <span className="self-start rounded-full bg-plum-soft px-2 py-0.5 text-2xs font-semibold text-plum-ink">{A.why.usual}</span> : null}
        <MarketCard item={it} variant="list" from="assist" />
      </div>,
    )
    const instead = (alts[code] || []).map((c) => itemsByCode.get(c)).filter((x): x is ShopItem => Boolean(x) && !done.has((x as ShopItem).item_code))
    if (instead.length) {
      out.push(
        <p key={`${code}-instead`} className="mt-1 inline-flex items-center gap-1.5 text-xs font-bold text-ok">
          <Check size={13} aria-hidden="true" />
          {A.why.alternative}
        </p>,
      )
      for (const a of instead) {
        done.add(a.item_code)
        out.push(<MarketCard key={a.item_code} item={a} variant="list" from="assist" />)
      }
    }
  }
  return out.length ? <div className="flex flex-col gap-2">{out}</div> : null
}

function HandoffCard({ m, onCallback }: { m: Msg; onCallback: (m: Msg) => Promise<string | null> }) {
  const { rep } = useMarket()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const url = m.handoff?.whatsapp_url || rep?.whatsapp_url || null
  const name = m.handoff?.name || rep?.first_name || rep?.name || null
  const codes = Array.from(new Set([...(m.items || []), ...(m.add || []).map((l) => l.item_code)]))
  if (m.receipt && (m.receipt.kind === 'callback' || m.receipt.kind === 'account')) return <Receipt r={m.receipt} />
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-surface p-3">
      <div className="flex items-center gap-2.5">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-plum-soft font-display text-xs font-bold text-plum-ink">{initials(name)}</span>
        <p className="min-w-0 flex-1 text-sm font-semibold text-ink">{name ? A.talkTo(name) : A.talkToRep}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {url ? (
          <AnchorButton variant="wa" size="sm" href={whatsappWith(url, m.question || '', codes)} target="_blank" rel="noreferrer" icon={<MessageCircle size={15} aria-hidden="true" />}>
            WhatsApp
          </AnchorButton>
        ) : (
          <p className="text-xs text-ink-2">{A.repLater}</p>
        )}
        {m.callback ? (
          <Button
            size="sm"
            variant="secondary"
            loading={busy}
            icon={<PhoneCall size={15} aria-hidden="true" />}
            onClick={async () => {
              setBusy(true)
              setErr((await onCallback(m)) || '')
              setBusy(false)
            }}
          >
            {A.callBack}
          </Button>
        ) : null}
      </div>
      {err ? <p className="text-xs text-bad">{err}</p> : null}
    </div>
  )
}

function ReplyActions({ m, onVote }: { m: Msg; onVote: (m: Msg, v: 'up' | 'down') => void }) {
  const toast = useToast()
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window
  const btn =
    'hit relative grid h-9 w-9 place-items-center rounded-full text-ink-3 hover:bg-plum-wash hover:text-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  return (
    <div className="flex items-center gap-0.5">
      {m.vote ? (
        <span className="px-1 text-xs text-ink-2">{A.thanksVote}</span>
      ) : (
        <>
          <button type="button" aria-label={A.helpful} title={A.helpful} onClick={() => onVote(m, 'up')} className={btn}>
            <ThumbsUp size={15} aria-hidden="true" />
          </button>
          <button type="button" aria-label={A.notHelpful} title={A.notHelpful} onClick={() => onVote(m, 'down')} className={btn}>
            <ThumbsDown size={15} aria-hidden="true" />
          </button>
        </>
      )}
      {m.text ? (
        <button
          type="button"
          aria-label={A.copy}
          title={A.copy}
          className={btn}
          onClick={() => {
            void navigator.clipboard?.writeText(m.text).then(() => toast(A.copied, 'success'), () => {})
          }}
        >
          <Copy size={15} aria-hidden="true" />
        </button>
      ) : null}
      {m.text && canSpeak ? (
        <button
          type="button"
          aria-label={A.readAloud}
          title={A.readAloud}
          className={btn}
          onClick={() => {
            const u = new SpeechSynthesisUtterance(m.text)
            u.lang = locale.lang === 'ar' ? 'ar-SA' : 'en-GB'
            window.speechSynthesis.cancel()
            window.speechSynthesis.speak(u)
          }}
        >
          <Volume2 size={15} aria-hidden="true" />
        </button>
      ) : null}
    </div>
  )
}

/* ───────────────────────── one turn ───────────────────────── */

function Turn({
  m,
  onRetry,
  onVote,
  onPatch,
  onStock,
  onTicket,
  onCallback,
}: {
  m: Msg
  onRetry: (m: Msg) => void
  onVote: (m: Msg, v: 'up' | 'down') => void
  onPatch: (id: string, p: Partial<Msg>) => void
  onStock: (text: string, turnId: number | null) => Promise<boolean>
  onTicket: (m: Msg) => SubmitTicket
  onCallback: (m: Msg) => Promise<string | null>
}) {
  const { itemsByCode, addMany, rep } = useMarket()
  const toast = useToast()
  const navigate = useNavigate()
  const [stockFailed, setStockFailed] = useState(false)

  if (m.role === 'user') {
    const pic = m.photo ? photoOf(m.id) : undefined
    return (
      <div className="ms-auto max-w-[86%] whitespace-pre-wrap break-words rounded-2xl rounded-ee-md bg-plum px-3.5 py-2.5 text-sm leading-relaxed text-white shadow-1">
        {pic ? <img src={pic} alt={A.photoAlt} className="mb-1.5 max-h-44 w-auto max-w-full rounded-lg" /> : null}
        {m.photo ? <Camera size={14} className="me-1.5 inline align-[-2px]" aria-hidden="true" /> : null}
        {m.text}
      </div>
    )
  }

  const wa = rep?.whatsapp_url || null

  if (m.error) {
    const line = m.error === 'photo' ? A.errPhoto : m.error === 'off' ? A.errOff : m.error === 'slow' ? A.errSlow : m.error === 'busy' ? A.errBusy : A.errNet
    return (
      <div className="flex gap-2.5">
        <Orb size={26} state="busy" />
        <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
          <p className="pt-0.5 text-sm leading-relaxed text-ink">{line}</p>
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
      </div>
    )
  }

  const lines = (m.add || []).map((l) => ({ item: itemsByCode.get(l.item_code), qty: l.qty })).filter((l): l is { item: ShopItem; qty: number } => Boolean(l.item))
  const hasRows = Boolean(m.rows?.length)
  const ticketOpen = Boolean(m.ticket)
  const wantsHandoff = !hasRows && !ticketOpen && (Boolean(m.escalated) || Boolean(m.handoff) || m.intent === 'handoff' || Boolean(m.miss))

  const addAll = () => {
    const n = addMany(lines, 'assist')
    onPatch(m.id, { added: true })
    if (n > 0) toast(A.added(n), { kind: 'success', action: { label: A.viewCart, onClick: () => navigate('/cart') } })
    else toast(A.addedNone, 'info')
  }

  return (
    <div className="flex gap-2.5">
      <Orb size={26} state="answered" />
      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {m.policy?.official ? (
          <span className="inline-flex items-center gap-1.5 self-start rounded-full bg-ok-soft px-2.5 py-0.5 text-2xs font-bold text-ok">
            <ShieldCheck size={12} aria-hidden="true" />
            {A.officialAnswer}
          </span>
        ) : null}
        {m.text ? <p className="whitespace-pre-wrap break-words pt-0.5 text-[15px] leading-relaxed text-ink">{m.text}</p> : null}
        {m.pending ? <p className="assist-stage text-xs font-semibold text-plum-ink">{A.stage.writing}</p> : null}
        {m.fallback ? <p className="text-xs font-semibold text-ink-2">{(m.searchCodes || []).length ? A.fallbackLine : A.fallbackNone}</p> : null}

        {!hasRows ? <Cards m={m} /> : null}
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
            {m.orders.map((o, i) => (
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
                    {[fmtDateShort(o.created_at), A.orderMeta(o.items, o.units), o.total_bhd != null ? withCurrency(moneyText(o.total_bhd)) : null].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <span className="text-xs font-semibold text-plum">{A.openOrder}</span>
                <ChevronRight size={16} className="text-ink-3 rtl:-scale-x-100" aria-hidden="true" />
              </Link>
            ))}
          </div>
        ) : null}

        {m.tickets?.length ? (
          <div className="overflow-hidden rounded-lg border border-line bg-surface" role="list" aria-label={A.myTickets}>
            {m.tickets.map((t, i) => (
              <div key={t.ref} role="listitem" className={cn('flex min-h-[52px] items-center gap-3 px-3.5 py-2.5', i > 0 && 'border-t border-line-2')}>
                <LifeBuoy size={16} className="shrink-0 text-plum" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <bdi dir="ltr" className="font-display text-sm font-bold text-ink">{t.ref}</bdi>
                  <span className="block text-xs text-ink-2">{[A.ticketKind[t.kind] || t.kind, fmtDateShort(t.created_at)].filter(Boolean).join(' · ')}</span>
                </span>
                <span className="rounded-full bg-plum-soft px-2 py-0.5 text-2xs font-semibold text-plum-ink">{A.ticketStatus[t.status] || t.status}</span>
              </div>
            ))}
          </div>
        ) : null}

        {ticketOpen ? <TicketCard m={m} onSubmit={onTicket(m)} /> : null}

        {m.miss && !hasRows ? (
          <div className="flex flex-wrap gap-2">
            {m.stocked ? (
              <p className="text-sm font-semibold text-ok">{A.askStockDone}</p>
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
            {stockFailed ? <p className="w-full text-xs text-bad">{A.errFeedback}</p> : null}
          </div>
        ) : null}

        {wantsHandoff ? <HandoffCard m={m} onCallback={onCallback} /> : null}

        {!m.pending && (m.turnId != null || m.text) ? <ReplyActions m={m} onVote={onVote} /> : null}
      </div>
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
        <p className="text-sm text-ink">{A.ideaThanks}</p>
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

/* ───────────────────────── the welcome: a greeting, six tiles, three chips ───────────────────────── */

function Welcome({ onTile, onChip }: { onTile: (action: AskAction, label: string) => void; onChip: (key: 'paste' | 'fit' | 'idea') => void }) {
  return (
    <div className="assist-rise flex flex-col gap-4">
      <div>
        <p className="font-display text-xl font-bold text-ink">{A.greeting(partOfDay())}</p>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">{A.welcome2}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {TILES.map(({ key, action, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => onTile(action, A.tiles[key])}
            className="group flex min-h-[76px] flex-col items-start justify-between gap-2 rounded-lg border border-line bg-surface p-3 text-start shadow-1 transition-[transform,border-color] duration-2 ease-m hover:-translate-y-0.5 hover:border-plum/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
          >
            <span className="grid h-8 w-8 place-items-center rounded-md bg-plum-wash text-plum">
              <Icon size={17} aria-hidden="true" />
            </span>
            <span>
              <span className="block text-sm font-semibold leading-tight text-ink">{A.tiles[key]}</span>
              <span className="mt-0.5 block text-2xs text-ink-3">{A.tileHint[key]}</span>
            </span>
          </button>
        ))}
      </div>
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
  )
}

/* ───────────────────────── the conversation ───────────────────────── */

export function AssistChat({ itemCode, first, onClose, titleId }: { itemCode: string | null; first?: { id: number; text: string } | null; onClose?: () => void; titleId?: string }) {
  const { msgs, itemCode: subject, busy, stage, slow, send, sendAction, sendPhoto, retryPhoto, reset, patch, feedback, vote, submitTicket } = useAssistChat(itemCode)
  const { itemsByCode, settings, stockAsOf } = useMarket()
  const navigate = useNavigate()
  const forced = enabledDevForced()
  const voiceOn = (settings.ai_voice === true || forced) && canRecord()
  const photoOn = settings.ai_photo === true || forced
  const reduced = useReducedMotion()
  const [draft, setDraft] = useState('')
  const [idea, setIdea] = useState(false)
  const [hint, setHint] = useState(false)
  const [menu, setMenu] = useState(false)
  const [about, setAbout] = useState(false)
  const [photoNote, setPhotoNote] = useState('')
  const camRef = useRef<HTMLInputElement>(null)
  const galRef = useRef<HTMLInputElement>(null)
  const handled = useRef<number | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const orbRef = useRef<HTMLSpanElement>(null)
  const barsRef = useRef<HTMLSpanElement>(null)
  const inputId = useId()
  const subjectItem = subject ? itemsByCode.get(subject) : undefined

  // always show the newest message (and the thinking stage); an empty chat stays at its greeting
  useLayoutEffect(() => {
    if (!msgs.length && !busy) return
    endRef.current?.scrollIntoView({ block: 'end', behavior: reduced ? 'auto' : 'smooth' })
  }, [msgs.length, busy, idea, reduced])

  const resize = useCallback(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [])

  const voice = useVoice(
    (text) => {
      setDraft((d) => (d.trim() ? `${d.trim()} ${text}` : text).slice(0, MAX_LEN))
      requestAnimationFrame(() => {
        resize()
        taRef.current?.focus({ preventScroll: true })
      })
    },
    // the voice's loudness, per frame, straight into CSS (the header orb and the bars only): no re-render
    (level) => {
      const v = level.toFixed(3)
      orbRef.current?.style.setProperty('--orb-level', v)
      barsRef.current?.style.setProperty('--lv', v)
    },
  )
  // opened from somewhere with a first message (a search that found nothing): send it once
  useEffect(() => {
    if (first && handled.current !== first.id) {
      handled.current = first.id
      send(first.text)
    }
  }, [first, send])

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

  const onChip = (key: 'paste' | 'fit' | 'idea') => {
    if (key === 'idea') setIdea(true)
    else if (key === 'paste') {
      setHint(true)
      taRef.current?.focus()
    } else submit(A.chips.fit)
  }

  const onCallback = async (m: Msg): Promise<string | null> => {
    const r = await submitTicket(m, { kind: 'callback', lines: [], client_ticket_id: newId() })
    return typeof r === 'string' ? r : null
  }

  const orbState: OrbState = voice.state === 'recording' ? 'listening' : busy ? (slow ? 'busy' : 'thinking') : 'idle'
  const asOf = fmtDateShort(stockAsOf)

  return (
    <div className="relative z-[1] flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-3 border-b border-line-2 px-4 pb-3 pt-3.5">
        <Orb ref={orbRef} size={38} state={orbState} />
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="font-display text-base font-bold leading-tight text-ink">
            {A.title}
          </h2>
          <p className={cn('mt-0.5 truncate text-xs leading-snug', busy ? 'assist-stage font-semibold text-plum-ink' : 'text-ink-2')}>
            {busy ? A.stage[stage || 'reading'] : A.subtitle}
          </p>
        </div>
        {msgs.length ? (
          <IconButton label={A.newChat} onClick={reset}>
            <RotateCcw size={17} aria-hidden="true" />
          </IconButton>
        ) : null}
        {onClose ? (
          <IconButton
            label={A.more}
            className="max-md:hidden"
            onClick={() => {
              navigate('/ask')
              onClose()
            }}
          >
            <Maximize2 size={16} aria-hidden="true" />
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
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4" role="log" aria-live="polite" aria-relevant="additions" aria-label={A.title}>
            {subjectItem ? (
              <p className="mb-3 truncate rounded-full bg-plum-soft px-3 py-1.5 text-xs font-semibold text-plum-ink" {...ltrText}>
                {A.aboutItem}: {productName(subjectItem)}
              </p>
            ) : null}

            {!msgs.length ? (
              <Welcome onTile={sendAction} onChip={onChip} />
            ) : (
              <div className="flex flex-col gap-5">
                {msgs.map((m) => (
                  <Turn
                    key={m.id}
                    m={m}
                    onRetry={(x) => (x.retryPhoto ? retryPhoto(x) : send(x.retryText || '', x.id))}
                    onVote={vote}
                    onPatch={patch}
                    onStock={(text, turnId) => feedback('idea', text, turnId)}
                    onTicket={(x) => (body) => submitTicket(x, body)}
                    onCallback={onCallback}
                  />
                ))}
              </div>
            )}

            {busy && !msgs.some((m) => m.pending) ? (
              <div className="mt-5">
                <Thinking stage={stage} slow={slow} />
              </div>
            ) : null}
            <div ref={endRef} aria-hidden="true" />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              submit(draft)
            }}
            className="border-t border-line-2 px-3 pb-[max(0.625rem,var(--m-safe-b))] pt-2.5"
          >
            {voice.note || photoNote ? (
              <p role="status" className="mb-2 rounded-md bg-bad-soft px-3 py-2 text-xs text-bad">
                {voice.note || photoNote}
              </p>
            ) : null}
            {voice.state !== 'idle' ? (
              <div role="status" className="flex min-h-12 items-center gap-3 rounded-xl border border-plum/30 bg-plum-wash px-3">
                {voice.state === 'recording' ? (
                  <span ref={barsRef} className="assist-bars" aria-hidden="true">
                    {BARS.map((k, i) => (
                      <span key={i} style={{ '--k': k } as CSSProperties} />
                    ))}
                  </span>
                ) : (
                  <Orb size={22} state="thinking" />
                )}
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
                  className="max-h-[120px] min-h-11 min-w-0 flex-1 resize-none rounded-xl border border-line bg-surface px-3.5 py-2.5 text-[16px] leading-snug text-ink placeholder:text-ink-3 focus-visible:border-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40 disabled:opacity-60"
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
            <div className="mt-1.5 flex items-start justify-between gap-3 px-1 text-2xs text-ink-3">
              <button type="button" onClick={() => setAbout((v) => !v)} aria-expanded={about} className="-my-2 inline-flex min-h-11 items-center gap-1 text-start hover:text-ink-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                <Info size={12} className="mt-px shrink-0" aria-hidden="true" />
                <span>{A.disclosure(asOf || null)}</span>
              </button>
              {draft.length >= 400 ? <span className="tnum shrink-0">{A.chars(draft.length, MAX_LEN)}</span> : null}
            </div>
            {about ? (
              <div className="mt-1.5 rounded-md bg-surface-2 px-3 py-2 text-xs leading-relaxed text-ink-2">
                <p className="font-semibold text-ink">{A.privacyTitle}</p>
                <p className="mt-0.5">{A.privacyBody}</p>
              </div>
            ) : null}
          </form>
        </>
      )}
    </div>
  )
}
