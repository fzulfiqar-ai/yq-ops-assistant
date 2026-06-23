import { useRef, useState, type FormEvent } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Send, Sparkles, Zap, Brain, Gauge, User, Database } from 'lucide-react'
import { apiPost } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/Logo'

type Model = 'pro' | 'thinking' | 'fast'
interface Msg {
  role: 'user' | 'assistant'
  content: string
  sql?: string
  cached?: boolean
  pending?: boolean
}

const MODELS: { id: Model; label: string; desc: string; icon: typeof Zap }[] = [
  { id: 'pro', label: 'Pro', desc: 'Most capable', icon: Brain },
  { id: 'thinking', label: 'Thinking', desc: 'Deep reasoning', icon: Gauge },
  { id: 'fast', label: 'Fast', desc: 'Quick answers', icon: Zap },
]

const SUGGESTIONS = [
  'What was our revenue this month?',
  'Top 5 customers by revenue',
  'Which items are low on stock?',
  'Total outstanding receivables',
]

function mdToHtml(s: string): string {
  const esc = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^### (.*)$/gm, '<div class="font-semibold mt-2">$1</div>')
    .replace(/\n/g, '<br/>')
}

function TypingDots() {
  return (
    <span className="inline-flex gap-1">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-current"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 1, repeat: Infinity, delay: i * 0.18 }}
        />
      ))}
    </span>
  )
}

export default function Assistant() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [model, setModel] = useState<Model>('pro')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  function scrollDown() {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }))
  }

  async function ask(question: string) {
    if (!question.trim() || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', content: question }, { role: 'assistant', content: '', pending: true }])
    scrollDown()
    try {
      const r = await apiPost<{ reply: string; sql_used?: string; cached?: boolean }>('/ask', { question, model })
      setMessages((m) => {
        const copy = [...m]
        copy[copy.length - 1] = { role: 'assistant', content: r.reply, sql: r.sql_used, cached: r.cached }
        return copy
      })
    } catch {
      setMessages((m) => {
        const copy = [...m]
        copy[copy.length - 1] = { role: 'assistant', content: 'Something went wrong. Please try again.' }
        return copy
      })
    } finally {
      setBusy(false)
      scrollDown()
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    ask(input)
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-9rem)] max-w-3xl flex-col">
      {/* Model selector */}
      <div className="mb-4 grid grid-cols-3 gap-2">
        {MODELS.map((mo) => {
          const Icon = mo.icon
          const active = model === mo.id
          return (
            <button
              key={mo.id}
              onClick={() => setModel(mo.id)}
              className={cn(
                'flex items-center gap-2.5 rounded-xl border p-3 text-left transition-all',
                active ? 'border-primary bg-accent shadow-soft' : 'border-border bg-card hover:border-primary/40',
              )}
            >
              <span className={cn('grid h-8 w-8 place-items-center rounded-lg', active ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
                <Icon size={16} />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold">{mo.label}</span>
                <span className="block truncate text-[11px] text-muted-foreground">{mo.desc}</span>
              </span>
            </button>
          )
        })}
      </div>

      {/* Conversation */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto rounded-2xl border bg-card/40 p-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <Logo float className="h-14 w-14 rounded-2xl shadow-lift" />
            <h2 className="mt-4 font-display text-xl font-bold">Ask anything about your business</h2>
            <p className="mt-1 text-sm text-muted-foreground">Sales, stock, margins, receivables — in plain English.</p>
            <div className="mt-5 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => ask(s)}
                  className="rounded-xl border bg-card px-3.5 py-2.5 text-left text-sm text-muted-foreground transition hover:border-primary/40 hover:text-foreground"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <AnimatePresence initial={false}>
              {messages.map((m, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                  className={cn('flex gap-3', m.role === 'user' ? 'justify-end' : 'justify-start')}
                >
                  {m.role === 'assistant' && (
                    <div className="grid h-8 w-8 shrink-0 place-items-center self-start rounded-lg bg-primary text-primary-foreground">
                      <Sparkles size={15} />
                    </div>
                  )}
                  <div
                    className={cn(
                      'max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
                      m.role === 'user'
                        ? 'rounded-br-md bg-primary text-primary-foreground'
                        : 'rounded-bl-md border bg-card',
                    )}
                  >
                    {m.pending ? (
                      <span className="text-muted-foreground">
                        <TypingDots />
                      </span>
                    ) : (
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
                    <div className="grid h-8 w-8 shrink-0 place-items-center self-start rounded-lg bg-muted text-muted-foreground">
                      <User size={15} />
                    </div>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={submit} className="mt-3 flex items-end gap-2">
        <div className="flex flex-1 items-center rounded-2xl border bg-card px-4 shadow-soft focus-within:border-primary">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about sales, stock, margins…"
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-soft transition hover:shadow-lift disabled:opacity-40"
        >
          <Send size={18} />
        </button>
      </form>
    </div>
  )
}
