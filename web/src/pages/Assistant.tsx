import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Send, Plus, Mic, MicOff, Trash2, MessageSquare, User, Database } from 'lucide-react'
import { apiPost } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/Logo'

type Model = 'pro' | 'thinking' | 'fast'
interface Msg { role: 'user' | 'assistant'; content: string; sql?: string; cached?: boolean; pending?: boolean }
interface Conversation { id: string; title: string; messages: Msg[]; updatedAt: number }

const MODELS: { id: Model; label: string }[] = [
  { id: 'pro', label: 'Pro' },
  { id: 'thinking', label: 'Thinking' },
  { id: 'fast', label: 'Fast' },
]
const SUGGESTIONS = [
  "Who's our top salesman this month?",
  'Which fast movers are out of stock?',
  'Who owes us the most, and how overdue?',
  "What's our gross margin?",
]
const STORE = 'yq-chats-v1'

function loadChats(): Conversation[] {
  try { return JSON.parse(localStorage.getItem(STORE) || '[]') } catch { return [] }
}
function saveChats(c: Conversation[]) {
  try { localStorage.setItem(STORE, JSON.stringify(c.slice(0, 30))) } catch { /* quota */ }
}
function newConv(): Conversation {
  return { id: Math.random().toString(36).slice(2), title: 'New chat', messages: [], updatedAt: Date.now() }
}
function mdToHtml(s: string): string {
  const esc = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^### (.*)$/gm, '<div class="font-semibold mt-2">$1</div>')
    .replace(/^[-•]\s(.*)$/gm, '<div class="flex gap-2"><span>•</span><span>$1</span></div>')
    .replace(/\n/g, '<br/>')
}
function TypingDots() {
  return (
    <span className="inline-flex gap-1">
      {[0, 1, 2].map((i) => (
        <motion.span key={i} className="h-1.5 w-1.5 rounded-full bg-current"
          animate={{ opacity: [0.3, 1, 0.3] }} transition={{ duration: 1, repeat: Infinity, delay: i * 0.18 }} />
      ))}
    </span>
  )
}

export default function Assistant() {
  const [chats, setChats] = useState<Conversation[]>(() => { const c = loadChats(); return c.length ? c : [newConv()] })
  const [activeId, setActiveId] = useState<string>(() => { const c = loadChats(); return (c[0]?.id) || '' })
  const [input, setInput] = useState('')
  const [model, setModel] = useState<Model>('pro')
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [lang, setLang] = useState<'en-US' | 'ar-SA'>('en-US')
  const scrollRef = useRef<HTMLDivElement>(null)
  const recogRef = useRef<any>(null)

  const active = chats.find((c) => c.id === activeId) || chats[0]
  useEffect(() => { if (!activeId && chats[0]) setActiveId(chats[0].id) }, [activeId, chats])
  useEffect(() => { saveChats(chats) }, [chats])
  function scrollDown() {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }))
  }

  function newChat() {
    const c = newConv()
    setChats((prev) => [c, ...prev])
    setActiveId(c.id)
    setInput('')
    setBusy(false)
  }
  function deleteChat(id: string) {
    setChats((prev) => {
      const next = prev.filter((c) => c.id !== id)
      const final = next.length ? next : [newConv()]
      if (id === activeId) setActiveId(final[0].id)
      return final
    })
  }

  async function ask(question: string) {
    const q = question.trim()
    if (!q || busy || !active) return
    setInput('')
    setBusy(true)
    const title = active.messages.length === 0 ? q.slice(0, 40) : active.title
    setChats((prev) => prev.map((c) => c.id === active.id
      ? { ...c, title, updatedAt: Date.now(), messages: [...c.messages, { role: 'user', content: q }, { role: 'assistant', content: '', pending: true }] }
      : c))
    scrollDown()
    try {
      const r = await apiPost<{ reply: string; sql_used?: string; cached?: boolean }>('/ask', { question: q, model })
      setChats((prev) => prev.map((c) => {
        if (c.id !== active.id) return c
        const msgs = [...c.messages]
        msgs[msgs.length - 1] = { role: 'assistant', content: r.reply, sql: r.sql_used, cached: r.cached }
        return { ...c, messages: msgs, updatedAt: Date.now() }
      }))
    } catch {
      setChats((prev) => prev.map((c) => {
        if (c.id !== active.id) return c
        const msgs = [...c.messages]
        msgs[msgs.length - 1] = { role: 'assistant', content: 'Something went wrong. Please try again.' }
        return { ...c, messages: msgs }
      }))
    } finally {
      setBusy(false)
      scrollDown()
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input) }
  }

  function toggleMic() {
    const SR = (window as any).webkitSpeechRecognition || (window as any).SpeechRecognition
    if (!SR) { alert('Voice dictation is not supported in this browser. Try Chrome or Edge.'); return }
    if (listening) { recogRef.current?.stop(); setListening(false); return }
    const rec = new SR()
    rec.lang = lang; rec.interimResults = true; rec.continuous = false
    rec.onresult = (ev: any) => {
      const text = Array.from(ev.results).map((r: any) => r[0].transcript).join('')
      setInput(text)
    }
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)
    recogRef.current = rec
    rec.start(); setListening(true)
  }

  const recent = [...chats].sort((a, b) => b.updatedAt - a.updatedAt)

  return (
    <div className="flex h-[calc(100vh-7.75rem)] gap-4">
      {/* Conversation rail */}
      <aside className="hidden w-60 shrink-0 flex-col rounded-2xl border bg-card/50 p-3 md:flex">
        <button onClick={newChat}
          className="mb-3 flex items-center justify-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-semibold text-primary-foreground shadow-soft transition hover:shadow-lift">
          <Plus size={16} /> New chat
        </button>
        <div className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Recent</div>
        <div className="flex-1 space-y-1 overflow-y-auto">
          {recent.map((c) => (
            <div key={c.id}
              className={cn('group flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition',
                c.id === activeId ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50')}>
              <button onClick={() => setActiveId(c.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                <MessageSquare size={14} className="shrink-0 text-muted-foreground" />
                <span className="truncate">{c.title}</span>
              </button>
              <button onClick={() => deleteChat(c.id)} className="opacity-0 transition group-hover:opacity-100" title="Delete">
                <Trash2 size={13} className="text-muted-foreground hover:text-rose-500" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Chat column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header */}
        <div className="mb-3 flex items-center gap-3 rounded-2xl border bg-card px-4 py-2.5">
          <Logo className="h-9 w-9 rounded-xl shadow-soft" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold">YQ AI Analyst</div>
            <div className="text-[11px] text-muted-foreground">Live ops data · as of 22 Jun 2026</div>
          </div>
          <div className="flex items-center gap-1 rounded-lg border bg-background p-0.5">
            {MODELS.map((mo) => (
              <button key={mo.id} onClick={() => setModel(mo.id)}
                className={cn('rounded-md px-2.5 py-1 text-xs font-medium transition',
                  model === mo.id ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>
                {mo.label}
              </button>
            ))}
          </div>
        </div>

        {/* Conversation */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto rounded-2xl border bg-card/40 p-4">
          {!active || active.messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <Logo float className="h-14 w-14 rounded-2xl shadow-lift" />
              <h2 className="mt-4 font-display text-xl font-bold">Ask anything about your business</h2>
              <p className="mt-1 text-sm text-muted-foreground">Sales, salesmen, stock, margins, receivables — in plain English.</p>
              <div className="mt-5 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => ask(s)}
                    className="rounded-xl border bg-card px-3.5 py-2.5 text-left text-sm text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <AnimatePresence initial={false}>
                {active.messages.map((m, i) => (
                  <motion.div key={i} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                    className={cn('flex gap-3', m.role === 'user' ? 'justify-end' : 'justify-start')}>
                    {m.role === 'assistant' && <Logo className="h-8 w-8 shrink-0 self-start rounded-lg shadow-soft" />}
                    <div className={cn('max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
                      m.role === 'user' ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md border bg-card')}>
                      {m.pending ? <span className="text-muted-foreground"><TypingDots /></span> : (
                        <>
                          <div dangerouslySetInnerHTML={{ __html: mdToHtml(m.content) }} />
                          {m.sql && (
                            <details className="mt-2 text-xs">
                              <summary className="flex cursor-pointer items-center gap-1.5 text-muted-foreground">
                                <Database size={12} /> SQL{m.cached ? ' · cached' : ''}
                              </summary>
                              <pre className="mt-1.5 overflow-x-auto rounded-lg bg-muted p-2 text-[11px] text-muted-foreground">{m.sql}</pre>
                            </details>
                          )}
                        </>
                      )}
                    </div>
                    {m.role === 'user' && (
                      <div className="grid h-8 w-8 shrink-0 place-items-center self-start rounded-lg bg-muted text-muted-foreground"><User size={15} /></div>
                    )}
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="mt-3">
          <div className="flex items-end gap-2 rounded-2xl border bg-card px-3 py-2 shadow-soft transition focus-within:border-primary/40 focus-within:ring-4 focus-within:ring-primary/10">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              rows={1}
              placeholder="Ask about sales, stock, margins, receivables…"
              className="max-h-32 min-h-[2.5rem] flex-1 resize-none bg-transparent py-1.5 text-sm outline-none placeholder:text-muted-foreground"
            />
            <button onClick={() => setLang((l) => (l === 'en-US' ? 'ar-SA' : 'en-US'))}
              className="shrink-0 rounded-md px-1.5 py-1 text-[10px] font-bold text-muted-foreground transition hover:text-foreground" title="Dictation language">
              {lang === 'en-US' ? 'EN' : 'AR'}
            </button>
            <button onClick={toggleMic} title="Dictate"
              className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-xl transition',
                listening ? 'bg-rose-500 text-white' : 'text-muted-foreground hover:bg-accent hover:text-foreground')}>
              {listening ? <MicOff size={17} /> : <Mic size={17} />}
            </button>
            <button onClick={() => ask(input)} disabled={busy || !input.trim()}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-soft transition hover:shadow-lift disabled:opacity-40">
              <Send size={17} />
            </button>
          </div>
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            Shift+Enter for new line · mic to dictate in English or Arabic · AI can make mistakes — verify important figures
          </p>
        </div>
      </div>
    </div>
  )
}
