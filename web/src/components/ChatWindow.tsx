// The conversation.
//
// Two columns. The question and its answer sit together with nothing between
// them, so the conversation reads as a conversation. The working -- which tools
// ran, on what, and which sources came back -- sits alongside, visible without
// interrupting the read and expandable when someone wants to audit it.
//
// A turn renders as it happens rather than on completion: tool cards appear in
// the order they run, so a reader watching sees the answer being reached.

import { useEffect, useRef, useState } from "react";
import type { SessionInfo, Turn } from "../types";
import { AnswerBody } from "./AnswerBody";
import { AsidePanel } from "./AsidePanel";
import { OpsView } from "./OpsView";
import { ConfirmActionCard } from "./ConfirmActionCard";
import { SourceCitation } from "./SourceCitation";
import { ToolCallCard } from "./ToolCallCard";

const SUGGESTIONS = [
  "Can I cancel ORD-1001 without a cancellation fee?",
  "A pickup is three hours late because of carrier fault. Do I get a service credit?",
  "What is my first response target for a P1?",
  "I want to escalate this to a human",
];


const TIER_NOUN: Record<string, [string, string]> = {
  AGREEMENT: ["agreement", "agreements"],
  CURRENT_POLICY: ["policy", "policies"],
  PRODUCT_DOC: ["product doc", "product docs"],
  HISTORICAL: ["past ticket", "past tickets"],
  DEPRECATED: ["superseded doc", "superseded docs"],
};

function describeWork(turn: Turn): string {
  if (!turn.done) {
    const running = turn.tools.find((call) => call.status === "running");
    return running ? "running…" : `${turn.tools.length} step(s)`;
  }
  const failed = turn.tools.filter((call) => call.status === "error").length;
  const steps = `${turn.tools.length} step${turn.tools.length === 1 ? "" : "s"}`;
  return failed ? `${steps} · ${failed} failed` : steps;
}

function describeSources(turn: Turn): string {
  const counts = new Map<string, number>();
  for (const source of turn.citations) {
    counts.set(source.authority_tier, (counts.get(source.authority_tier) ?? 0) + 1);
  }
  const parts = [...counts.entries()].map(([tier, count]) => {
    const [one, many] = TIER_NOUN[tier] ?? [tier, tier];
    return `${count} ${count === 1 ? one : many}`;
  });
  return parts.join(", ");
}

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
  // Staff get a second view. Customers never see the tab, and the endpoint behind
  // it refuses them anyway -- the tab is convenience, the refusal is the control.
  const [tab, setTab] = useState<"chat" | "ops">("chat");
  const latest = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Scroll to the top of the newest turn, not the bottom of the page. The sources
  // panel makes a turn several screens tall, and scrolling to the end of it lands
  // the reader on citations for an answer they have not seen yet.
  useEffect(() => {
    latest.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length]);

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
        {session.internal && (
          <nav className="tabs">
            <button
              className={`tab ${tab === "chat" ? "tab--on" : ""}`}
              onClick={() => setTab("chat")}
              type="button"
            >
              Chat
            </button>
            <button
              className={`tab ${tab === "ops" ? "tab--on" : ""}`}
              onClick={() => setTab("ops")}
              type="button"
            >
              Needs attention
            </button>
          </nav>
        )}

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

      {tab === "ops" ? (
        <OpsView
          onAsk={(question) => {
            setTab("chat");
            onAsk(question);
          }}
        />
      ) : (
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
          <article
            className="turn"
            key={index}
            ref={index === turns.length - 1 ? latest : undefined}
          >
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
                <AsidePanel
                  // Held open while the turn runs: with no answer yet, watching the
                  // tools work is the content.
                  open={!turn.done ? true : undefined}
                  summary={describeWork(turn)}
                  title="Work"
                >
                  <div className="panel__stack">
                    {turn.tools.map((call) => (
                      <ToolCallCard call={call} key={`${call.step}-${call.name}`} />
                    ))}
                  </div>
                </AsidePanel>
              )}

              {turn.conflicts.length > 0 && (
                // Never folded away. Two lines, and it is the finding the whole
                // authority hierarchy exists to produce.
                <section className="conflicts">
                  <h3>Sources disagree</h3>
                  {turn.conflicts.map((conflict) => (
                    <p key={conflict.explanation}>{conflict.explanation}</p>
                  ))}
                </section>
              )}

              {turn.citations.length > 0 && (
                <AsidePanel summary={describeSources(turn)} title="Sources">
                  <div className="panel__stack">
                    {turn.citations.map((source) => (
                      <SourceCitation key={source.citation} source={source} />
                    ))}
                  </div>
                </AsidePanel>
              )}
            </aside>
          </article>
        ))}

      </div>
      )}

      {error && <p className="failure failure--banner">{error}</p>}

      {tab === "chat" && (
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
      )}
    </div>
  );
}
