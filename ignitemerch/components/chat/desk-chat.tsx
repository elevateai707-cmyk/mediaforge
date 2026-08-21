"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

interface ChatSource {
  label: string;
  href: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: ChatSource[];
}

export function DeskChat() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "hello",
      role: "assistant",
      text: "Desk chat. I answer from the FAQ and live lots. I do not invent prices or run counts.",
    },
  ]);
  const titleId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (!open) openerRef.current?.focus();
  }, [open]);

  async function send(next: string) {
    const message = next.trim();
    if (!message || pending) return;
    setError("");
    setText("");
    setMessages((current) => [
      ...current,
      { id: `u-${Date.now()}`, role: "user", text: message },
    ]);
    setPending(true);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const payload = (await response.json()) as {
        text?: string;
        sources?: ChatSource[];
        error?: string;
      };
      if (!response.ok) {
        setError(payload.error ?? "Chat failed.");
        return;
      }
      setMessages((current) => [
        ...current,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: payload.text ?? "",
          sources: payload.sources,
        },
      ]);
    } catch {
      setError("Network error. Try the FAQ page.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button
        ref={openerRef}
        type="button"
        className="fixed bottom-5 right-5 z-50 inline-flex h-12 min-w-12 items-center bg-ember px-4 font-mono text-[11px] uppercase tracking-[0.16em] text-soot transition-transform duration-200 ease-expo hover:bg-ember-hot active:scale-[0.96]"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        Ask the desk
      </button>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-end justify-end p-4 md:p-8">
          <button
            type="button"
            className="absolute inset-0 bg-soot/50"
            aria-label="Dismiss chat"
            onClick={() => setOpen(false)}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="relative flex h-[min(32rem,80vh)] w-full max-w-md flex-col border border-bone/15 bg-ink"
          >
            <div className="flex items-center justify-between border-b border-bone/10 px-4 py-3">
              <h2 id={titleId} className="font-display text-2xl font-extrabold uppercase">
                Desk
              </h2>
              <button
                type="button"
                className="inline-flex h-11 min-w-11 items-center justify-center border border-bone/20 font-mono text-[11px] uppercase tracking-[0.14em]"
                onClick={() => setOpen(false)}
              >
                Close
              </button>
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
              {messages.map((message) => (
                <div key={message.id}>
                  <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-steel">
                    {message.role === "user" ? "You" : "Desk"}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-bone">
                    {message.text}
                  </p>
                  {message.sources?.length ? (
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {message.sources.map((source) => (
                        <li key={source.href}>
                          <Link
                            href={source.href}
                            className="font-mono text-[11px] uppercase tracking-[0.12em] text-ember underline"
                          >
                            {source.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
              {pending ? (
                <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-ash">
                  Looking up the catalog
                </p>
              ) : null}
            </div>
            <form
              className="border-t border-bone/10 p-3"
              onSubmit={(event) => {
                event.preventDefault();
                void send(text);
              }}
            >
              <label className="block">
                <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ash">
                  Question
                </span>
                <input
                  ref={inputRef}
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  maxLength={500}
                  className="mt-2 h-12 w-full border border-bone/15 bg-soot px-3 text-bone"
                  placeholder="How do downloads work?"
                />
              </label>
              {error ? (
                <p className="mt-2 text-sm text-ember" role="alert">
                  {error}
                </p>
              ) : null}
              <button
                type="submit"
                disabled={pending}
                className="mt-3 inline-flex h-12 items-center bg-ember px-4 font-mono text-[11px] uppercase tracking-[0.16em] text-soot disabled:opacity-50"
              >
                Send
              </button>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
