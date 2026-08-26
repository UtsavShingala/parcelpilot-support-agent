// The mock login, and the access-control demo in one screen.
//
// Switching persona here is what makes scoping visible: the same question, asked
// as two customers, comes back with different material because the tool layer
// gave them different material. On a public URL it doubles as the gate -- no
// persona, no chat, enforced server-side.

import type { Persona, Roster } from "../types";

export function PersonaPicker({
  roster,
  onChoose,
  busy,
}: {
  roster: Roster;
  onChoose: (persona: Persona) => void;
  busy: boolean;
}) {
  return (
    <div className="picker">
      <header className="picker__head">
        <h1>ParcelPilot Support</h1>
        <p className="picker__lede">
          Choose who you are signing in as. Every answer is scoped to that identity in
          the data layer, not by asking the assistant to behave.
        </p>
      </header>

      <ul className="picker__list">
        {roster.personas.map((persona) => (
          <li key={persona.persona_id}>
            <button
              className="persona"
              disabled={busy}
              onClick={() => onChoose(persona)}
              type="button"
            >
              <span className="persona__label">{persona.label}</span>
              <span className="persona__description">{persona.description}</span>
            </button>
          </li>
        ))}
      </ul>

      <footer className="picker__foot">
        <p>
          Data snapshot <strong>{new Date(roster.snapshot_at).toLocaleString()}</strong> —
          all timing is measured against this instant, never the current clock.
        </p>
        <p className={`mode mode--${roster.mode}`}>{roster.mode_description}</p>
      </footer>
    </div>
  );
}
