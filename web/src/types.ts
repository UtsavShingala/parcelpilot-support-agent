// The wire contract, mirroring parcelpilot/agent/events.py.
//
// Deliberately narrow: the browser is told what happened, never what it is
// allowed to do. There is no role or account here, because the server does not
// send them and would not believe them if the browser sent them back.

export interface Persona {
  persona_id: string;
  label: string;
  description: string;
}

export interface SessionInfo {
  persona: Persona;
  /** Whether this caller is ParcelPilot staff. Decides which views are offered;
      the endpoints enforce it independently. */
  internal: boolean;
  messages_remaining: number;
  messages_allowed: number;
  snapshot_at: string;
  mode: string;
  mode_description: string;
}

export interface Roster {
  personas: Persona[];
  snapshot_at: string;
  mode: string;
  mode_description: string;
}

export type AuthorityTier =
  | "AGREEMENT"
  | "CURRENT_POLICY"
  | "PRODUCT_DOC"
  | "HISTORICAL"
  | "DEPRECATED";

export interface Citation {
  citation: string;
  source_file: string;
  version: string | null;
  clause: string;
  authority_tier: AuthorityTier;
  applies_to: string;
  status: string;
  effective_date: string | null;
  text: string;
}

export interface Conflict {
  kind: string;
  explanation: string;
  governing: string;
  subordinate: string;
}

export interface ToolResultPayload {
  result_count?: number;
  visible_scope?: string;
  results?: Citation[];
  conflicts?: Conflict[];
  orders?: Record<string, unknown>[];
  tickets?: Record<string, unknown>[];
  note?: string | null;
  [key: string]: unknown;
}

export interface ActionDraft {
  draft_id: string;
  kind: string;
  summary: string;
  details: Record<string, unknown>;
  account_id: string | null;
  prepared_for: string;
  status: string;
}

export type AgentEvent =
  | { type: "session_status"; message: string }
  | {
      type: "tool_start";
      name: string;
      arguments: Record<string, unknown>;
      step: number;
      mutating: boolean;
      call_id: string;
    }
  | {
      type: "tool_result";
      name: string;
      ok: boolean;
      step: number;
      summary: string;
      error: string | null;
      mutating: boolean;
      call_id: string;
      result?: ToolResultPayload;
    }
  | { type: "text_delta"; text: string; final: boolean }
  | { type: "action_draft"; draft: ActionDraft }
  | { type: "escalation"; reason: string; detail: string; draft?: ActionDraft }
  | { type: "completed"; text: string; steps: number; escalated: boolean }
  | { type: "failed"; message: string };

// One tool call, assembled from its start and result events.
export interface ToolCall {
  /** Identifies this call. A step is shared by every call in one model reply, so
      matching a result on step alone updates the wrong card. */
  callId: string;
  step: number;
  name: string;
  arguments: Record<string, unknown>;
  mutating: boolean;
  status: "running" | "ok" | "error";
  summary?: string;
  error?: string | null;
  result?: ToolResultPayload;
}

export interface Turn {
  question: string;
  tools: ToolCall[];
  answer: string;
  drafts: ActionDraft[];
  escalation?: { reason: string; detail: string };
  citations: Citation[];
  conflicts: Conflict[];
  failure?: string;
  done: boolean;
}

// -- operations view ------------------------------------------------------------

export interface Signal {
  kind: string;
  severity: "P1" | "P2" | "P3" | "info";
  title: string;
  detail: string;
  tickets: string[];
  accounts: string[];
  citations: string[];
  elapsed_minutes: number | null;
  target: string | null;
}

export interface ActionRecord {
  action_id: number;
  draft_id: string;
  kind: string;
  summary: string;
  details: Record<string, unknown>;
  account_id: string | null;
  performed_by: string;
  effective_at: string;
  recorded_at: string;
}

export interface InsightsView {
  snapshot_at: string;
  scope: string;
  signals: Signal[];
  counts: Record<string, number>;
  escalations: ActionRecord[];
}
