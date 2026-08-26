"""Account-scoped reads over the operational tables.

Every query is built with the caller's account restriction **inside the SQL
predicate**, not applied to the rows afterwards. The difference matters more than
it looks:

* A post-filter means the wrong rows were fetched, so a bug in the filter, an early
  return, a ``LIMIT`` applied before it, or a log line printing the raw result set
  all leak. Rows that never left SQLite cannot leak by accident.
* ``LIMIT`` composes correctly. "The five most recent orders" means five of *this
  customer's* orders, not five of everyone's, filtered down to however many survive.

A customer context with no account resolves to a predicate that matches nothing,
so a mistake upstream fails closed.

Historical ticket resolutions are returned but flagged. The workbook states plainly
that some of them are wrong, so they travel with a warning attached rather than
being silently omitted -- they are useful context for an agent investigating a
pattern, and dangerous as an answer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from parcelpilot.auth.context import CallerContext
from parcelpilot.config import Settings
from parcelpilot.data.database import connect, snapshot_time

DEFAULT_LIMIT = 25

HISTORICAL_RESOLUTION_WARNING = (
    "Historical resolutions are past agent replies, not policy. The dataset states "
    "some are incorrect; verify against current policy before repeating one."
)


@dataclass(frozen=True)
class ScopePredicate:
    """A SQL fragment and its parameters, restricting rows to what a caller may read."""

    sql: str
    parameters: tuple[Any, ...]

    @classmethod
    def for_caller(cls, caller: CallerContext, column: str = "account_id") -> ScopePredicate:
        if caller.is_internal:
            return cls("1 = 1", ())
        if caller.account_id:
            return cls(f"{column} = ?", (caller.account_id,))
        return cls("1 = 0", ())  # fail closed


class OperationalData:
    """Read access to accounts, orders and tickets, scoped to the caller."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._snapshot = snapshot_time(connection)

    @classmethod
    def open(cls, settings: Settings | None = None) -> OperationalData:
        return cls(connect(settings))

    @property
    def snapshot_at(self) -> datetime:
        """The reference time for every elapsed-time calculation."""
        return self._snapshot

    def close(self) -> None:
        self._connection.close()

    # -- accounts ---------------------------------------------------------------

    def accounts(self, caller: CallerContext) -> list[dict[str, Any]]:
        scope = ScopePredicate.for_caller(caller)
        return self._rows(
            f"SELECT * FROM accounts WHERE {scope.sql} ORDER BY account_id", scope.parameters
        )

    def account(self, caller: CallerContext, account_id: str) -> dict[str, Any] | None:
        scope = ScopePredicate.for_caller(caller)
        rows = self._rows(
            f"SELECT * FROM accounts WHERE {scope.sql} AND account_id = ?",
            (*scope.parameters, account_id),
        )
        return rows[0] if rows else None

    # -- orders -----------------------------------------------------------------

    def orders(
        self,
        caller: CallerContext,
        *,
        order_id: str | None = None,
        account_id: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        scope = ScopePredicate.for_caller(caller)
        clauses = [scope.sql]
        parameters: list[Any] = list(scope.parameters)

        if order_id:
            clauses.append("order_id = ?")
            parameters.append(order_id)
        if account_id:
            clauses.append("account_id = ?")
            parameters.append(account_id)
        if status:
            clauses.append("UPPER(status) = UPPER(?)")
            parameters.append(status)

        parameters.append(limit)
        return self._rows(
            f"SELECT * FROM orders WHERE {' AND '.join(clauses)} "
            "ORDER BY booked_at DESC LIMIT ?",
            tuple(parameters),
        )

    def order(self, caller: CallerContext, order_id: str) -> dict[str, Any] | None:
        rows = self.orders(caller, order_id=order_id, limit=1)
        return rows[0] if rows else None

    # -- tickets ----------------------------------------------------------------

    def tickets(
        self,
        caller: CallerContext,
        *,
        ticket_id: str | None = None,
        account_id: str | None = None,
        status: str | None = None,
        assigned_to: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        scope = ScopePredicate.for_caller(caller)
        clauses = [scope.sql]
        parameters: list[Any] = list(scope.parameters)

        if ticket_id:
            clauses.append("ticket_id = ?")
            parameters.append(ticket_id)
        if account_id:
            clauses.append("account_id = ?")
            parameters.append(account_id)
        if status:
            clauses.append("LOWER(status) = LOWER(?)")
            parameters.append(status)
        if assigned_to:
            clauses.append("LOWER(assigned_to) = LOWER(?)")
            parameters.append(assigned_to)

        parameters.append(limit)
        rows = self._rows(
            f"SELECT * FROM tickets WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT ?",
            tuple(parameters),
        )
        return [self._flag_historical_resolution(row) for row in rows]

    def ticket(self, caller: CallerContext, ticket_id: str) -> dict[str, Any] | None:
        rows = self.tickets(caller, ticket_id=ticket_id, limit=1)
        return rows[0] if rows else None

    def ticket_exists(self, caller: CallerContext, ticket_id: str) -> bool:
        """Whether the caller may act on this ticket. Used before preparing an action."""
        return self.ticket(caller, ticket_id) is not None

    # -- internals --------------------------------------------------------------

    def _rows(self, sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = self._connection.execute(sql, parameters)
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _flag_historical_resolution(row: dict[str, Any]) -> dict[str, Any]:
        if row.get("historical_resolution"):
            row["historical_resolution_warning"] = HISTORICAL_RESOLUTION_WARNING
        return row
