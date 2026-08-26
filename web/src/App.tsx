// Wiring: roster, session, and the reducer that turns a stream of events into a turn.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, askQuestion, fetchRoster, fetchSession, signIn, signOut } from "./api";
import { ChatWindow } from "./components/ChatWindow";
import { PersonaPicker } from "./components/PersonaPicker";
import type { AgentEvent, Roster, SessionInfo, Turn } from "./types";

function blankTurn(question: string): Turn {
  return {
    question,
    tools: [],
    answer: "",
    drafts: [],
    citations: [],
    conflicts: [],
    done: false,
  };
}

/**
 * Fold one event into the turn being built.
 *
 * Pure, and returns a new object every time: the stream arrives faster than React
 * renders, and mutating in place would drop frames.
 */
function applyEvent(turn: Turn, event: AgentEvent): Turn {
  switch (event.type) {
    case "tool_start":
      return {
        ...turn,
        tools: [
          ...turn.tools,
          {
            step: event.step,
            name: event.name,
            arguments: event.arguments,
            mutating: event.mutating,
            status: "running",
          },
        ],
      };

    case "tool_result": {
      const tools = turn.tools.map((call) =>
        call.step === event.step && call.name === event.name && call.status === "running"
          ? {
              ...call,
              status: event.ok ? ("ok" as const) : ("error" as const),
              summary: event.summary,
              error: event.error,
              result: event.result,
            }
          : call,
      );
      // Citations accumulate across tool calls; a later search must not erase an
      // earlier one's sources, and the same clause must not be listed twice.
      const seen = new Set(turn.citations.map((item) => item.citation));
      const citations = [
        ...turn.citations,
        ...(event.result?.results ?? []).filter((item) => !seen.has(item.citation)),
      ];
      const known = new Set(turn.conflicts.map((item) => item.explanation));
      const conflicts = [
        ...turn.conflicts,
        ...(event.result?.conflicts ?? []).filter((item) => !known.has(item.explanation)),
      ];
      return { ...turn, tools, citations, conflicts };
    }

    case "text_delta":
      return event.final ? turn : { ...turn, answer: turn.answer + event.text };

    case "action_draft":
      return turn.drafts.some((draft) => draft.draft_id === event.draft.draft_id)
        ? turn
        : { ...turn, drafts: [...turn.drafts, event.draft] };

    case "escalation":
      return { ...turn, escalation: { reason: event.reason, detail: event.detail } };

    case "completed":
      return { ...turn, answer: event.text, done: true };

    case "failed":
      return { ...turn, failure: event.message, done: true };

    default:
      return turn;
  }
}

export default function App() {
  const [roster, setRoster] = useState<Roster | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetchRoster(), fetchSession()])
      .then(([loadedRoster, existing]) => {
        setRoster(loadedRoster);
        setSession(existing);
      })
      .catch((cause) => setError(String(cause)))
      .finally(() => setLoading(false));
  }, []);

  const choose = useCallback(async (personaId: string) => {
    setBusy(true);
    setError(null);
    try {
      setSession(await signIn(personaId));
      setTurns([]);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, []);

  const leave = useCallback(async () => {
    await signOut();
    setSession(null);
    setTurns([]);
  }, []);

  const ask = useCallback(async (question: string) => {
    setBusy(true);
    setError(null);
    setTurns((previous) => [...previous, blankTurn(question)]);

    const update = (event: AgentEvent) =>
      setTurns((previous) => {
        const next = [...previous];
        next[next.length - 1] = applyEvent(next[next.length - 1], event);
        return next;
      });

    try {
      await askQuestion(question, update);
      setSession(await fetchSession());
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
      if (cause instanceof ApiError && cause.status === 401) setSession(null);
    } finally {
      setBusy(false);
    }
  }, []);

  const body = useMemo(() => {
    if (loading) return <p className="boot">Loading…</p>;
    if (!roster) return <p className="failure">{error ?? "The service is unreachable."}</p>;
    if (!session) {
      return (
        <PersonaPicker
          busy={busy}
          onChoose={(persona) => choose(persona.persona_id)}
          roster={roster}
        />
      );
    }
    return (
      <ChatWindow
        busy={busy}
        error={error}
        onAsk={ask}
        onSignOut={leave}
        session={session}
        turns={turns}
      />
    );
  }, [ask, busy, choose, error, leave, loading, roster, session, turns]);

  return <main className="app">{body}</main>;
}
