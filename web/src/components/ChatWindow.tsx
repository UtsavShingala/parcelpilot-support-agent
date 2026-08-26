// The conversation.
//
// A turn is rendered as it happens: tool cards appear in the order they run, the
// answer arrives after them, and the sources it rested on sit underneath. The
// ordering is deliberate -- the work is shown before the conclusion, so the
// conclusion reads as something that was reached rather than asserted.

import { useEffect, useRef } from "react";
import type { SessionInfo, Turn } from "../types";
import { ConfirmActionCard } from "./ConfirmActionCard";
import { SourceCitation } from "./SourceCitation";
import { ToolCallCard } from "./ToolCallCard";

const SUGGESTIONS = [
  "Can I cancel ORD-1001 without a cancellation fee?",
  "A pickup is three hours late because of carrier fault. Do I get a service credit?",
  "What is my first response target for a P1?",
  "I want to escalate this to a human",
];

export function ChatWindow({
  session,
  turns,
  busy,
  error,
  onAsk,
  onSignOut,
}: {
  session: SessionInfo;
  turns: Turn[];
  busy: boolean;
  error: string | null;
  onAsk: (question: string) => void;
  onSignOut: () => void;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const question = input.current?.value.trim();
    if (!question || busy) return;
    onAsk(question);
    if (input.current) input.current.value = "";
  }

  const exhausted = session.messages_remaining <= 0;

  return (
    <div className="chat">
      <header className="chat__head">
        <div>
          <strong>{session.persona.label}</strong>
          <span className="chat__persona">{session.persona.description}</span>
        </div>
        <div className="chat__right">
          <span className={`mode mode--${session.mode}`} title={session.mode_description}>
            {session.mode}
          </span>
          <span className="chat__quota">
            {session.messages_remaining}/{session.messages_allowed} messages left
          </span>
          <button className="button" onClick={onSignOut} type="button">
            Switch persona
          </button>
        </div>
      </header>

      <div className="chat__scroll">
        {turns.length === 0 && (
          <div className="empty">
            <p>
              Ask about cancellations, service credits, response targets, or a known
              issue. Answers come only from the supplied documents.
            </p>
            <ul className="empty__suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <li key={suggestion}>
                  <button
                    className="button button--ghost"
                    disabled={busy}
                    onClick={() => onAsk(suggestion)}
                    type="button"
                  >
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {turns.map((turn, index) => (
          <article className="turn" key={index}>
            <p className="turn__question">{turn.question}</p>

            {turn.tools.length > 0 && (
              <section className="turn__tools">
                {turn.tools.map((call) => (
                  <ToolCallCard call={call} key={`${call.step}-${call.name}`} />
                ))}
              </section>
            )}

            {turn.conflicts.length > 0 && (
              <section className="conflicts">
                <h3>Sources disagree</h3>
                {turn.conflicts.map((conflict) => (
                  <p key={conflict.explanation}>{conflict.explanation}</p>
                ))}
              </section>
            )}

            {turn.escalation && (
              <section className="escalation">
                <strong>Handed to a person.</strong> {turn.escalation.detail}
              </section>
            )}

            {turn.answer && <div className="turn__answer">{turn.answer}</div>}

            {turn.drafts.map((draft) => (
              <ConfirmActionCard draft={draft} key={draft.draft_id} />
            ))}

            {turn.citations.length > 0 && (
              <section className="turn__sources">
                <h3>Sources</h3>
                {turn.citations.map((source) => (
                  <SourceCitation key={source.citation} source={source} />
                ))}
              </section>
            )}

            {turn.failure && <p className="failure">{turn.failure}</p>}
          </article>
        ))}

        {busy && <p className="thinking">Working…</p>}
        <div ref={bottom} />
      </div>

      {error && <p className="failure failure--banner">{error}</p>}

      <form className="composer" onSubmit={submit}>
        <textarea
          disabled={busy || exhausted}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) submit(event);
          }}
          placeholder={
            exhausted
              ? "This session has used its message allowance."
              : "Ask a support question…"
          }
          ref={input}
          rows={2}
        />
        <button
          className="button button--primary"
          disabled={busy || exhausted}
          type="submit"
        >
          Ask
        </button>
      </form>
    </div>
  );
}
