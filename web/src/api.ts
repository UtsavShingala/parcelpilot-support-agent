// Talking to the service.
//
// The chat endpoint is a POST that streams, which EventSource cannot do -- it only
// issues GETs. So the SSE frames are parsed off a fetch body reader here. That is
// the right trade anyway: the question belongs in a body rather than a query
// string, and the session cookie rides along without any of EventSource's
// cross-origin caveats.

import type { AgentEvent, InsightsView, Roster, SessionInfo } from "./types";

const JSON_HEADERS = { "Content-Type": "application/json" };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // A non-JSON error body is still an error; keep the status text.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export async function fetchRoster(): Promise<Roster> {
  return unwrap<Roster>(await fetch("/api/personas"));
}

export async function fetchSession(): Promise<SessionInfo | null> {
  const response = await fetch("/api/session");
  if (response.status === 401) return null; // not signed in yet; not an error
  return unwrap<SessionInfo>(response);
}

export async function signIn(personaId: string): Promise<SessionInfo> {
  return unwrap<SessionInfo>(
    await fetch("/api/session", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ persona_id: personaId }),
    }),
  );
}

export async function fetchInsights(): Promise<InsightsView> {
  return unwrap<InsightsView>(await fetch("/api/insights"));
}

export async function signOut(): Promise<void> {
  await fetch("/api/session", { method: "DELETE" });
}

export async function confirmAction(draftId: string): Promise<{ status: string }> {
  return unwrap<{ status: string }>(
    await fetch("/api/actions/confirm", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ draft_id: draftId }),
    }),
  );
}

/**
 * Ask a question, invoking `onEvent` for each frame as it arrives.
 *
 * Frames are separated by a blank line and may straddle chunk boundaries, so the
 * tail of each read is carried forward rather than parsed eagerly.
 */
export async function askQuestion(
  question: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ question }),
    signal,
  });

  if (!response.ok || !response.body) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? ""; // the last piece may be incomplete
    for (const frame of frames) {
      const event = parseFrame(frame);
      if (event) onEvent(event);
    }
  }

  const trailing = parseFrame(buffer);
  if (trailing) onEvent(trailing);
}

function parseFrame(frame: string): AgentEvent | null {
  const line = frame.split("\n").find((candidate) => candidate.startsWith("data: "));
  if (!line) return null;
  try {
    return JSON.parse(line.slice("data: ".length)) as AgentEvent;
  } catch {
    return null; // a truncated frame is not worth crashing the page over
  }
}
