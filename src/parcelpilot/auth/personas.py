"""The mock login: who you can sign in as.

Personas are read out of the operational tables rather than written down here. A
hard-coded list would be a second copy of the account roster, and the moment the
workbook changed it would be a *stale* copy -- offering a sign-in for an account
that no longer exists, or omitting one that does. The brief also says evaluators
may test with different records, so the roster has to follow the data.

Customer personas come from the accounts table. Internal personas come from the
names tickets are actually assigned to, plus one operations manager: the workbook
has no staff table, and the set of people handling tickets is the closest thing it
records to a support roster.

This runs before any caller exists, so it reads the tables directly rather than
through the scoped query layer -- there is nobody to scope it to yet. It therefore
exposes only what a login screen needs: an id, a label and a role.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.config import Settings
from parcelpilot.data.database import connect

OPS_MANAGER_PERSONA_ID = "ops-manager"


@dataclass(frozen=True)
class Persona:
    """A selectable identity, and the caller context signing in as it produces."""

    persona_id: str
    label: str
    description: str
    context: CallerContext
    public_description: str = ""

    @property
    def role(self) -> Role:
        return self.context.role

    def to_dict(self) -> dict[str, str]:
        """The full record, for server-side use and the CLI."""
        return {
            "persona_id": self.persona_id,
            "label": self.label,
            "description": self.description,
            "role": self.context.role.value,
            "account_id": self.context.account_id or "",
        }

    def to_public_dict(self) -> dict[str, str]:
        """What a browser is allowed to know.

        Role and account id are deliberately absent. The browser picks an identity
        by ``persona_id`` once; from then on its only credential is an opaque
        session id, and the authority behind it is resolved server-side. Nothing
        here is ever read back as an authorisation claim.
        """
        return {
            "persona_id": self.persona_id,
            "label": self.label,
            "description": self.public_description or self.description,
        }


def load_personas(connection: sqlite3.Connection) -> list[Persona]:
    """Build the sign-in roster from the data, customers first."""
    return [*_customer_personas(connection), *_internal_personas(connection)]


def open_personas(settings: Settings | None = None) -> list[Persona]:
    connection = connect(settings)
    try:
        return load_personas(connection)
    finally:
        connection.close()


def find_persona(personas: list[Persona], persona_id: str) -> Persona | None:
    return next((persona for persona in personas if persona.persona_id == persona_id), None)


def _customer_personas(connection: sqlite3.Connection) -> list[Persona]:
    rows = connection.execute(
        "SELECT account_id, account_name, plan FROM accounts ORDER BY account_id"
    ).fetchall()
    return [
        Persona(
            persona_id=str(row["account_id"]).lower(),
            label=str(row["account_name"]),
            description=f"{row['plan']} plan customer, account {row['account_id']}",
            public_description=f"{row['plan']} plan customer",
            context=CallerContext(
                role=Role.CUSTOMER,
                account_id=str(row["account_id"]),
                display_name=str(row["account_name"]),
            ),
        )
        for row in rows
    ]


def _internal_personas(connection: sqlite3.Connection) -> list[Persona]:
    """Support agents from whoever handles tickets, plus one operations manager."""
    rows = connection.execute(
        "SELECT DISTINCT assigned_to FROM tickets "
        "WHERE assigned_to IS NOT NULL AND TRIM(assigned_to) != '' "
        "ORDER BY assigned_to"
    ).fetchall()

    personas = [
        Persona(
            persona_id=f"agent-{str(row['assigned_to']).strip().lower()}",
            label=str(row["assigned_to"]).strip(),
            description="ParcelPilot support agent",
            context=CallerContext(
                role=Role.SUPPORT_AGENT, display_name=str(row["assigned_to"]).strip()
            ),
        )
        for row in rows
    ]
    personas.append(
        Persona(
            persona_id=OPS_MANAGER_PERSONA_ID,
            label="Operations manager",
            description="ParcelPilot operations, with approval authority",
            context=CallerContext(role=Role.OPS_MANAGER, display_name="Operations manager"),
        )
    )
    return personas
