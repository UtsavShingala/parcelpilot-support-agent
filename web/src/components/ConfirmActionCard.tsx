// The confirmation gate.
//
// This card is the only path from a prepared action to a performed one. The model
// cannot press it, and the request it sends carries a draft id rather than the
// action itself, so nothing here can be edited into something the server did not
// prepare.

import { useState } from "react";
import { ApiError, confirmAction } from "../api";
import type { ActionDraft } from "../types";

type State = "pending" | "working" | "confirmed" | "declined" | "error";

const KIND_LABEL: Record<string, string> = {
  escalation: "Escalate to a human",
  ticket_update: "Update a ticket",
  follow_up: "Create a follow-up task",
};

export function ConfirmActionCard({ draft }: { draft: ActionDraft }) {
  const [state, setState] = useState<State>("pending");
  const [message, setMessage] = useState("");

  async function confirm() {
    setState("working");
    try {
      const result = await confirmAction(draft.draft_id);
      setMessage(
        result.status === "already recorded"
          ? "Already recorded — it was not filed twice."
          : "Recorded. A support agent will pick this up.",
      );
      setState("confirmed");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : String(error));
      setState("error");
    }
  }

  return (
    <div className={`confirm confirm--${state}`}>
      <div className="confirm__head">
        <span className="confirm__kind">{KIND_LABEL[draft.kind] ?? draft.kind}</span>
        <span className="confirm__status">
          {state === "pending" ? "Nothing has happened yet" : draft.status}
        </span>
      </div>

      <p className="confirm__summary">{draft.summary}</p>

      <dl className="confirm__details">
        {Object.entries(draft.details).map(([key, value]) => (
          <div key={key}>
            <dt>{key.replace(/_/g, " ")}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>

      {state === "pending" && (
        <div className="confirm__actions">
          <button className="button button--primary" onClick={confirm} type="button">
            Confirm
          </button>
          <button
            className="button"
            onClick={() => {
              setMessage("Not confirmed. Nothing was written.");
              setState("declined");
            }}
            type="button"
          >
            Cancel
          </button>
        </div>
      )}

      {state === "working" && <p className="confirm__message">Recording…</p>}
      {message && state !== "working" && <p className="confirm__message">{message}</p>}
    </div>
  );
}
