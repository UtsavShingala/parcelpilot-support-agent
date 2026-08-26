"""Drive the agent from a terminal.

Exists to answer one question before any interface is built: does the same question,
asked by two different customers, produce two correctly different answers? That is
the whole system in one command -- retrieval scoped by account, the customer's own
agreement outranking the general SOP, arithmetic against the snapshot, and a
handover when a person is needed.

It also runs the confirmation flow honestly. Drafts are held in memory for the
duration of the run and only written if you say yes at the prompt, which is exactly
how the transport layer will do it later.

    python -m parcelpilot.cli personas
    python -m parcelpilot.cli ask "Can I cancel ORD-1001 without a fee?" -p acct-001
    python -m parcelpilot.cli compare "Am I owed a service credit?" -p acct-001 -p acct-002
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from parcelpilot.agent.events import (
    ActionDrafted,
    Escalated,
    Failed,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from parcelpilot.agent.loop import SupportAgent, Turn, collect
from parcelpilot.agent.model import ModelClient, ModelUnavailable, available_models
from parcelpilot.agent.provider import build_model_client
from parcelpilot.agent.registry import build_registry
from parcelpilot.agent.tools.actions import ActionLedger
from parcelpilot.auth.personas import Persona, find_persona, open_personas
from parcelpilot.config import Settings, get_settings
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

RULE = "=" * 78
THIN = "-" * 78


@dataclass
class Session:
    """Everything one run of the CLI needs, built once."""

    settings: Settings
    data: OperationalData
    agent: SupportAgent
    ledger: ActionLedger
    personas: list[Persona]

    def close(self) -> None:
        self.data.close()
        self.ledger.close()


def build_session(settings: Settings, client: ModelClient | None = None) -> Session:
    data = OperationalData.open(settings)
    store = DocumentStore.from_settings(settings)
    registry = build_registry(store, data)
    model = client or build_model_client(settings)
    return Session(
        settings=settings,
        data=data,
        agent=SupportAgent(
            registry=registry,
            client=model,
            snapshot_at=data.snapshot_at,
            max_steps=settings.max_agent_steps,
            max_seconds=settings.max_turn_seconds,
        ),
        ledger=ActionLedger(settings.actions_path, effective_at=data.snapshot_at),
        personas=open_personas(settings),
    )


# -- rendering ------------------------------------------------------------------


def _print_header(persona: Persona, question: str) -> None:
    print(f"\n{RULE}")
    print(f"{persona.label}  [{persona.context.describe()}]")
    print(f"{THIN}")
    print(f"Q: {question}\n")


def _render(events: Sequence[object]) -> None:
    for event in events:
        if isinstance(event, ToolStarted):
            flag = " (awaits confirmation)" if event.mutating else ""
            print(f"  -> {event.name}{flag} {_compact(event.arguments)}")
        elif isinstance(event, ToolFinished):
            mark = "ok " if event.ok else "ERR"
            print(f"     [{mark}] {event.summary}")
        elif isinstance(event, ActionDrafted):
            print(f"     [draft] {event.draft.draft_id}  {event.draft.summary}")
        elif isinstance(event, Escalated):
            print(f"     [escalation] {event.reason}")
        elif isinstance(event, TextDelta) and not event.final:
            print(f"  .. {event.text}")
        elif isinstance(event, Failed):
            print(f"\n  FAILED: {event.message}")


def _compact(arguments: dict[str, object]) -> str:
    rendered = json.dumps(arguments, default=str)
    return rendered if len(rendered) <= 96 else rendered[:93] + "..."


def _print_answer(turn: Turn) -> None:
    print(f"\nA: {turn.answer}\n")
    tail = f"{turn.steps} step(s)"
    if turn.escalated:
        tail += " | escalated to a human"
    if turn.drafts:
        tail += f" | {len(turn.drafts)} draft(s) awaiting confirmation"
    print(f"   [{tail}]")


# -- confirmation ---------------------------------------------------------------


def _offer_confirmation(session: Session, persona: Persona, turn: Turn, assume: str) -> None:
    """Prepare/confirm, run honestly: nothing is written until a person says yes."""
    for draft in turn.drafts:
        print(f"\n{THIN}")
        print(f"Awaiting confirmation: {draft.summary}")
        for key, value in draft.details.items():
            print(f"   {key}: {value}")

        answer = assume or _ask(f"Confirm this {draft.kind.value}? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("   not confirmed; nothing was written")
            continue

        record = session.ledger.confirm(draft, persona.context)
        print(f"   confirmed -> action #{record.action_id} recorded at {record.recorded_at}")


def _ask(prompt: str) -> str:
    if not sys.stdin.isatty():
        print(f"{prompt}(no terminal attached; not confirming)")
        return "n"
    return input(prompt)


# -- commands -------------------------------------------------------------------


def _resolve(session: Session, persona_id: str) -> Persona:
    persona = find_persona(session.personas, persona_id)
    if persona is None:
        known = ", ".join(item.persona_id for item in session.personas)
        raise SystemExit(f"unknown persona {persona_id!r}; try one of: {known}")
    return persona


def _run_one(session: Session, persona: Persona, question: str, assume: str) -> Turn:
    _print_header(persona, question)
    turn = collect(session.agent.run(persona.context, question))
    _render(turn.events)
    _print_answer(turn)
    if turn.drafts:
        _offer_confirmation(session, persona, turn, assume)
    return turn


def command_personas(session: Session) -> int:
    print(f"\nSign in as any of these ({len(session.personas)} from the workbook):\n")
    for persona in session.personas:
        print(f"  {persona.persona_id:<14} {persona.label:<22} {persona.description}")
    print(f"\nDataset snapshot: {session.data.snapshot_at.isoformat()}")
    return 0


def command_ask(session: Session, question: str, persona_ids: list[str], assume: str) -> int:
    for persona_id in persona_ids:
        _run_one(session, _resolve(session, persona_id), question, assume)
    return 0


def command_compare(
    session: Session, question: str, persona_ids: list[str], assume: str
) -> int:
    if len(persona_ids) < 2:
        raise SystemExit("compare needs at least two personas; pass -p twice")

    turns = [
        (persona_id, _run_one(session, _resolve(session, persona_id), question, assume))
        for persona_id in persona_ids
    ]

    print(f"\n{RULE}")
    print("Same question, different callers")
    print(THIN)
    for persona_id, turn in turns:
        persona = _resolve(session, persona_id)
        tools = [
            event.name for event in turn.events if isinstance(event, ToolStarted)
        ]
        print(f"\n{persona.label} ({persona.context.account_id or persona.role.value})")
        print(f"   tools    : {', '.join(tools) or 'none'}")
        print(f"   escalated: {turn.escalated}")
        print(f"   answer   : {turn.answer[:400]}")

    answers = {turn.answer.strip() for _, turn in turns}
    print(f"\n{THIN}")
    print(
        "Answers differ by caller."
        if len(answers) > 1
        else "WARNING: every caller received an identical answer."
    )
    return 0


def command_models(settings: Settings) -> int:
    """List the models this key can reach, so the configured id is never a guess."""
    try:
        models = available_models(
            api_key=settings.model_api_key, base_url=settings.model_base_url
        )
    except ModelUnavailable as error:
        print(f"\nCannot list models: {error}")
        return 2

    print(f"\n{len(models)} model(s) available to this key, newest first:\n")
    for model_id in models:
        marker = "   <- configured in .env" if model_id == settings.model_name else ""
        print(f"  {model_id}{marker}")

    if settings.model_name not in models:
        print(
            f"\nWARNING: OPENAI_MODEL is {settings.openai_model!r}, which is not in this "
            "list. Every request will fail until .env is changed."
        )
    print("\nPick the cheapest one that supports tool calling; check your pricing page.")
    return 0


def command_ledger(session: Session, persona_id: str) -> int:
    persona = _resolve(session, persona_id)
    records = session.ledger.records(persona.context)
    print(f"\nActions visible to {persona.label} ({len(records)}):\n")
    for record in records:
        print(f"  #{record.action_id} {record.kind.value:<14} {record.summary}")
        print(f"      by {record.performed_by} | effective {record.effective_at}")
    if not records:
        print("  (none)")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    # Accepted before or after the subcommand. argparse only honours a top-level
    # option ahead of the subcommand, and "--scripted" typed at the end of a long
    # question is the natural thing to write, so every subparser inherits it too.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--scripted",
        action="store_true",
        help="answer with the deterministic client instead of calling a provider",
    )

    parser = argparse.ArgumentParser(
        prog="parcelpilot", description=__doc__, parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "personas", help="list the sign-in roster read from the workbook", parents=[common]
    )

    ask = sub.add_parser("ask", help="ask a question as one or more personas", parents=[common])
    ask.add_argument("question")
    ask.add_argument("-p", "--persona", action="append", required=True, dest="personas")
    ask.add_argument(
        "--confirm-all",
        action="store_const",
        const="y",
        default="",
        help="confirm every prepared action without prompting",
    )

    compare = sub.add_parser(
        "compare", help="ask the same question as several personas", parents=[common]
    )
    compare.add_argument("question")
    compare.add_argument("-p", "--persona", action="append", required=True, dest="personas")
    compare.add_argument(
        "--confirm-all", action="store_const", const="y", default="n", dest="confirm_all"
    )

    sub.add_parser(
        "models", help="list models this key can reach, and flag a stale id", parents=[common]
    )

    ledger = sub.add_parser(
        "ledger", help="show confirmed actions a persona may see", parents=[common]
    )
    ledger.add_argument("-p", "--persona", required=True)

    arguments = parser.parse_args(argv)
    settings = get_settings()
    if arguments.scripted:
        settings = settings.model_copy(update={"scripted": True})

    if arguments.command == "models":  # talks to the provider, builds no session
        return command_models(settings)

    try:
        session = build_session(settings)
    except ModelUnavailable as error:
        if arguments.command in {"personas", "ledger"}:
            session = build_session(settings, client=_NoModel())
        else:
            print(f"\nCannot reach a model: {error}")
            print("Copy .env.example to .env and set OPENAI_API_KEY, then try again.")
            return 2

    try:
        if arguments.command == "personas":
            return command_personas(session)
        if arguments.command == "ask":
            return command_ask(
                session, arguments.question, arguments.personas, arguments.confirm_all
            )
        if arguments.command == "compare":
            return command_compare(
                session, arguments.question, arguments.personas, arguments.confirm_all
            )
        if arguments.command == "ledger":
            return command_ledger(session, arguments.persona)
    finally:
        session.close()
    return 0


class _NoModel:
    """Stands in for a model on commands that never call one."""

    def reply(self, *, messages: object, tools: object) -> object:  # pragma: no cover
        raise ModelUnavailable("no model configured")


if __name__ == "__main__":
    raise SystemExit(main())
