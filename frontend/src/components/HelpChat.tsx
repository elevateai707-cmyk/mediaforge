import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUp, MessageCircle, Sparkles, X } from 'lucide-react'
import {
  HELP_OPEN_EVENT,
  HELP_STARTERS,
  answerHelp,
  contextualGreeting,
  type HelpReply,
} from '@/lib/help-kb'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ChatTurn {
  id: string
  role: 'user' | 'agent'
  text?: string
  reply?: HelpReply
}

function newId(): string {
  return `m-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function ReplyBody({ reply, onFollow }: { reply: HelpReply; onFollow: (q: string) => void }) {
  return (
    <div className="space-y-2.5">
      {reply.title && (
        <p className="font-display text-[15px] font-medium leading-snug text-foreground">
          {reply.title}
        </p>
      )}
      {reply.paragraphs.map((p) => (
        <p key={p} className="text-[13px] leading-relaxed text-foreground/85">
          {p}
        </p>
      ))}
      {reply.bullets && reply.bullets.length > 0 && (
        <ul className="space-y-1.5 pl-0">
          {reply.bullets.map((b) => (
            <li
              key={b}
              className="flex gap-2 text-[13px] leading-relaxed text-foreground/80"
            >
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}
      {reply.links && reply.links.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {reply.links.map((l) => (
            <Link
              key={l.to}
              to={l.to}
              className="inline-flex h-8 items-center rounded-full border border-primary/30 bg-primary/10 px-3 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
            >
              {l.label}
            </Link>
          ))}
        </div>
      )}
      {reply.followUps.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {reply.followUps.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onFollow(q)}
              className="cursor-pointer rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function HelpChat() {
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>(() => [
    { id: 'hello', role: 'agent', reply: contextualGreeting(location.pathname) },
  ])
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const pendingRef = useRef(false)
  const askRef = useRef<(q: string) => void>(() => {})

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns, pending, open])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      const tag = (e.target as HTMLElement | null)?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement | null)?.isContentEditable
      if (e.key === '?' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault()
        setOpen(true)
      }
      if (e.key === 'Escape' && open) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => {
    function onOpen(e: Event) {
      const detail = (e as CustomEvent<{ q?: string }>).detail
      setOpen(true)
      if (detail?.q) askRef.current(detail.q)
    }
    window.addEventListener(HELP_OPEN_EVENT, onOpen)
    return () => window.removeEventListener(HELP_OPEN_EVENT, onOpen)
  }, [])

  function ask(raw: string) {
    const q = raw.trim()
    if (!q || pendingRef.current) return
    setInput('')
    setTurns((t) => [...t, { id: newId(), role: 'user', text: q }])
    pendingRef.current = true
    setPending(true)
    window.setTimeout(() => {
      setTurns((t) => [...t, { id: newId(), role: 'agent', reply: answerHelp(q) }])
      pendingRef.current = false
      setPending(false)
    }, 280)
  }
  askRef.current = ask

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    ask(input)
  }

  function onInputKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      ask(input)
    }
  }

  return (
    <>
      <AnimatePresence>
        {open && (
          <motion.section
            initial={{ opacity: 0, y: 10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.22, ease: [0.2, 0, 0, 1] }}
            className="glass-strong fixed bottom-24 right-4 z-50 flex h-[min(34rem,calc(100vh-8rem))] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl md:bottom-6"
            aria-label="Forge help"
          >
            <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
              <div className="flex items-center gap-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-secondary">
                  <Sparkles className="h-4 w-4 text-primary-foreground" />
                </div>
                <div>
                  <p className="font-display text-sm leading-none">Forge</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">Cutting-room guide · local</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="flex h-10 w-10 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-white/5 hover:text-foreground"
                aria-label="Close help"
              >
                <X className="h-4 w-4" />
              </button>
            </header>

            <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {turns.map((turn) => (
                <div
                  key={turn.id}
                  className={cn('flex', turn.role === 'user' ? 'justify-end' : 'justify-start')}
                >
                  {turn.role === 'user' ? (
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3 py-2 text-[13px] text-primary-foreground">
                      {turn.text}
                    </div>
                  ) : (
                    turn.reply && (
                      <div className="max-w-[95%] rounded-2xl rounded-bl-md border border-border bg-white/[0.03] px-3 py-3">
                        <ReplyBody reply={turn.reply} onFollow={ask} />
                      </div>
                    )
                  )}
                </div>
              ))}
              {pending && (
                <div className="flex justify-start">
                  <div className="flex gap-1 rounded-2xl border border-border bg-white/[0.03] px-3 py-2.5">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary [animation-delay:120ms]" />
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-secondary [animation-delay:240ms]" />
                  </div>
                </div>
              )}
            </div>

            <form onSubmit={onSubmit} className="border-t border-border p-3">
              <div className="flex items-center gap-2 rounded-xl border border-border bg-background/60 px-2 py-1">
                <input
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onInputKey}
                  placeholder="Ask how to scan, search, or cut a reel…"
                  className="h-9 min-w-0 flex-1 bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground/70"
                  aria-label="Ask Forge"
                />
                <Button
                  type="submit"
                  size="icon"
                  variant="gradient"
                  disabled={!input.trim() || pending}
                  aria-label="Send"
                >
                  <ArrowUp className="h-4 w-4" />
                </Button>
              </div>
              {!turns.some((t) => t.role === 'user') && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {HELP_STARTERS.map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => ask(q)}
                      className="cursor-pointer rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              )}
            </form>
          </motion.section>
        )}
      </AnimatePresence>

      {!open && (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-20 right-4 z-50 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-secondary text-primary-foreground shadow-[0_12px_40px_-8px] shadow-primary/50 transition-[transform,box-shadow] duration-200 ease-out hover:brightness-110 active:scale-[0.96] md:bottom-5"
        aria-label="Open Forge help"
        aria-expanded={false}
      >
        <MessageCircle className="h-5 w-5" />
      </button>
      )}
    </>
  )
}
