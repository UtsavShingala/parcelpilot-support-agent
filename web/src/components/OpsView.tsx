// What needs attention, for the people who act on it.
//
// The chat answers a question someone thought to ask. This answers the one nobody
// asked yet: which response targets are already missed, which fault is generating
// several tickets, and which past answers the current documents contradict.
//
// Every row shows its evidence -- the tickets it rests on and the clause it was
// measured against -- because a dashboard that says "3 issues need attention"
// without saying which, or why, stops being opened.

import { useEffect, useState } from "react";
import { ApiError, fetchInsights } from "../api";
import type { ActionRecord, InsightsView, Signal } from "../types";

const KIND_LABEL: Record<string, string> = {
  sla_breached: "Response overdue",
  sla_at_risk: "Response due soon",
  needs_manual_check: "Needs a person",
  issue_cluster: "Recurring issue",
  multi_account_issue: "Affects several customers",
  unverified_past_answer: "Past answer unverified",
};

export function OpsView({ onAsk }: { onAsk: (question: string) => void }) {
  const [view, setView] = useState<InsightsView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchInsights()
      .then(setView)
      .catch((cause) =>
        setError(cause instanceof ApiError ? cause.message : String(cause)),
      );
  }, []);

  if (error) return <p className="failure">{error}</p>;
  if (!view) return <p className="boot">Reading the queue…</p>;

  return (
    <div className="ops">
      <header className="ops__head">
        <div>
          <h2>Needs attention</h2>
          <p className="ops__scope">
            {view.signals.length} signal{view.signals.length === 1 ? "" : "s"} across{" "}
            {view.scope}, measured against the data snapshot of{" "}
            {new Date(view.snapshot_at).toLocaleString()}.
          </p>
        </div>
        <div className="ops__counts">
          {(["P1", "P2", "P3"] as const).map((level) =>
            view.counts[level] ? (
              <span className={`tally tally--${level.toLowerCase()}`} key={level}>
                {view.counts[level]} {level}
              </span>
            ) : null,
          )}
        </div>
      </header>

      {view.signals.length === 0 && <p className="empty">Nothing is overdue right now.</p>}

      <ul className="ops__list">
        {view.signals.map((signal, index) => (
          <SignalRow key={index} onAsk={onAsk} signal={signal} />
        ))}
      </ul>

      <section className="ops__escalations">
        <h3>Confirmed escalations ({view.escalations.length})</h3>
        {view.escalations.length === 0 ? (
          <p className="empty">Nobody has handed anything over yet.</p>
        ) : (
          <ul>
            {view.escalations.map((record) => (
              <EscalationRow key={record.draft_id} record={record} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function SignalRow({ signal, onAsk }: { signal: Signal; onAsk: (q: string) => void }) {
  const ticket = signal.tickets[0];
  return (
    <li className={`signal signal--${signal.severity.toLowerCase()}`}>
      <div className="signal__head">
        <span className={`tally tally--${signal.severity.toLowerCase()}`}>
          {signal.severity}
        </span>
        <span className="signal__kind">{KIND_LABEL[signal.kind] ?? signal.kind}</span>
        <span className="signal__title">{signal.title}</span>
      </div>

      <p className="signal__detail">{signal.detail}</p>

      <div className="signal__meta">
        {signal.tickets.map((id) => (
          <code key={id}>{id}</code>
        ))}
        {signal.accounts.map((id) => (
          <span className="signal__account" key={id}>
            {id}
          </span>
        ))}
        {signal.citations.map((citation) => (
          <span className="signal__cite" key={citation}>
            {citation}
          </span>
        ))}
        {ticket && (
          // Straight from a finding into the assistant, carrying the ticket with it.
          <button
            className="button button--ghost signal__ask"
            onClick={() => onAsk(`What should we do about ${ticket}?`)}
            type="button"
          >
            Ask about {ticket}
          </button>
        )}
      </div>
    </li>
  );
}

function EscalationRow({ record }: { record: ActionRecord }) {
  return (
    <li className="escalation-row">
      <div>
        <strong>#{record.action_id}</strong> {record.summary}
      </div>
      <div className="escalation-row__meta">
        {record.account_id && <span>{record.account_id}</span>}
        <span>raised by {record.performed_by}</span>
        <span title="The dataset snapshot this was decided against">
          effective {new Date(record.effective_at).toLocaleString()}
        </span>
      </div>
    </li>
  );
}
