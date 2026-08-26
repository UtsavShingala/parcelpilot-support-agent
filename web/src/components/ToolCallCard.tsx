// Which tool is running, on what, and what came back.
//
// This is the interface being honest about its work. The card appears the moment
// a call starts and updates when it returns, so "searching the agreements" is a
// statement of fact rather than a loading animation with a caption.

import { useState } from "react";
import type { ToolCall } from "../types";

const TOOL_LABEL: Record<string, string> = {
  search_documents: "Searching policies and agreements",
  lookup_orders: "Looking up orders",
  lookup_tickets: "Looking up tickets",
  lookup_account: "Reading account details",
  calculate: "Calculating",
  prepare_escalation: "Drafting an escalation",
  prepare_ticket_update: "Drafting a ticket update",
  prepare_follow_up: "Drafting a follow-up",
};

export function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const label = TOOL_LABEL[call.name] ?? call.name;

  return (
    <div className={`tool tool--${call.status}`}>
      <button className="tool__head" onClick={() => setOpen(!open)} type="button">
        <span className={`tool__dot tool__dot--${call.status}`} aria-hidden="true" />
        <span className="tool__name">{label}</span>
        {call.mutating && (
          <span className="tool__badge" title="Prepares only; nothing is written">
            needs confirmation
          </span>
        )}
        <span className="tool__summary">
          {call.status === "running" ? "running…" : (call.error ?? call.summary)}
        </span>
        <span className="tool__chevron">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="tool__body">
          <div className="tool__row">
            <span className="tool__key">tool</span>
            <code>{call.name}</code>
          </div>
          <div className="tool__row">
            <span className="tool__key">arguments</span>
            <code>{JSON.stringify(call.arguments)}</code>
          </div>
          {call.result?.visible_scope && (
            <div className="tool__row">
              <span className="tool__key">scope</span>
              <code>{call.result.visible_scope}</code>
            </div>
          )}
          {call.result?.note && <p className="tool__note">{call.result.note}</p>}
        </div>
      )}
    </div>
  );
}
