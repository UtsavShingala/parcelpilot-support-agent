// The conversation.
//
// Two columns. The question and its answer sit together with nothing between
// them, so the conversation reads as a conversation. The working -- which tools
// ran, on what, and which sources came back -- sits alongside, visible without
// interrupting the read and expandable when someone wants to audit it.
//
// A turn renders as it happens rather than on completion: tool cards appear in
// the order they run, so a reader watching sees the answer being reached.

import { useEffect, useRef } from "react";
import type { SessionInfo, Turn } from "../types";
import { AnswerBody } from "./AnswerBody";
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
            {/* The conversation itself: question, then answer, then anything the
                reader has to act on. Nothing between the question and its answer. */}
            <div className="turn__main">
              <p className="turn__question">{turn.question}</p>

              {turn.answer ? (
                <div className="turn__answer">
                  <AnswerBody text={turn.answer} />
                </div>
              ) : (
                !turn.failure && <p className="thinking">Working…</p>
              )}

              {turn.escalation && (
                <section className="escalation">
                  <strong>Handed to a person.</strong> {turn.escalation.detail}
                </section>
              )}

              {turn.drafts.map((draft) => (
                <ConfirmActionCard draft={draft} key={draft.draft_id} />
              ))}

              {turn.failure && <p className="failure">{turn.failure}</p>}
            </div>

            {/* The working: what it did and what it read. Beside the answer rather
                than above it, so the conversation stays readable and the evidence
                stays available. */}
            <aside className="turn__aside">
              {turn.tools.length > 0 && (
                <section className="turn__tools">
                  <h3>Work</h3>
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

              {turn.citations.length > 0 && (
                <section className="turn__sources">
                  <h3>Sources</h3>
                  {turn.citations.map((source) => (
                    <SourceCitation key={source.citation} source={source} />
                  ))}
                </section>
              )}
            </aside>
          </article>
        ))}

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
