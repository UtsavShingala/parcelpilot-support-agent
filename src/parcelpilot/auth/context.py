"""Who is asking, and what that entitles them to see.

A :class:`CallerContext` is the single argument every tool takes first. It is
frozen, so nothing downstream can widen its own access part-way through a request,
and it carries no permission logic of its own -- it *derives* an
:class:`~parcelpilot.retrieval.scope.AccountScope`, which is where the rules about
who may read which account already live. Two implementations of the same rule would
eventually disagree, and the one that disagreed silently would be the leak.

Roles are coarse on purpose. The distinction that matters is whether the caller is
a customer, who sees exactly one account, or ParcelPilot staff, who see all of
them; everything finer is about which *tools* a role may reach, which the registry
handles.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from parcelpilot.retrieval.scope import AccountScope


class Role(StrEnum):
    """What kind of user is asking."""

    CUSTOMER = "customer"
    """A ParcelPilot customer, confined to their own account."""

    SUPPORT_AGENT = "support_agent"
    """Internal support staff. Reads every account; may act on tickets."""

    OPS_MANAGER = "ops_manager"
    """Internal operations. Everything support can do, plus approvals."""


INTERNAL_ROLES = frozenset({Role.SUPPORT_AGENT, Role.OPS_MANAGER})


@dataclass(frozen=True)
class CallerContext:
    """An authenticated caller. Frozen: access cannot widen mid-request."""

    role: Role
    account_id: str | None = None
    display_name: str = ""

    def __post_init__(self) -> None:
        if self.role is Role.CUSTOMER and not self.account_id:
            raise ValueError("a customer context must name the account it belongs to")
        if self.is_internal and self.account_id:
            raise ValueError(
                "an internal context must not be pinned to one account; internal staff "
                "are scoped by role, and a stray account_id would read as a restriction "
                "that nothing enforces"
            )

    @property
    def is_internal(self) -> bool:
        return self.role in INTERNAL_ROLES

    @property
    def is_customer(self) -> bool:
        return self.role is Role.CUSTOMER

    def account_scope(self) -> AccountScope:
        """The material this caller may read.

        Delegates to the retrieval layer's scope rules rather than restating them.
        """
        if self.is_internal:
            return AccountScope.unrestricted_access()
        if self.account_id:
            return AccountScope.for_accounts(self.account_id)
        return AccountScope.none()

    def describe(self) -> str:
        who = self.display_name or self.role.value
        return f"{who} ({self.role.value}, {self.account_scope().describe()})"
