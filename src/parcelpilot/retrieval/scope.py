"""Which account-scoped material a caller is allowed to see.

Customer agreements are the only documents in the pack that belong to somebody.
They are also the ones most worth reading -- an agreement states rates, credit
amounts and named staff -- so a retrieval layer that returns them to whoever asks
the right question leaks one customer's contract to another.

The filter lives here, below the agent, because instructing a model not to mention
another account's contract is not access control: it is a request. A passage the
caller may not see is never returned, so it cannot be quoted, summarised or
paraphrased.

Access is denied by default. :meth:`AccountScope.none` is the zero value, so code
that forgets to pass a caller's scope sees general policy only, rather than
everything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from parcelpilot.ingest.authority import GLOBAL_SCOPE


@dataclass(frozen=True)
class AccountScope:
    """The set of accounts whose private material a caller may read."""

    accounts: frozenset[str] = field(default_factory=frozenset)
    unrestricted: bool = False

    @classmethod
    def none(cls) -> AccountScope:
        """No account-scoped material at all. The default, and the safe one."""
        return cls()

    @classmethod
    def for_accounts(cls, *account_ids: str) -> AccountScope:
        """A customer context: general material plus these accounts' own documents."""
        return cls(accounts=frozenset(account_ids))

    @classmethod
    def unrestricted_access(cls) -> AccountScope:
        """An internal context, entitled to every account's material."""
        return cls(unrestricted=True)

    def permits(self, scope: str) -> bool:
        """Whether material carrying ``scope`` may be shown to this caller."""
        if scope == GLOBAL_SCOPE:
            return True
        return self.unrestricted or scope in self.accounts

    def filter(self, scopes: Iterable[str]) -> list[str]:
        return [scope for scope in scopes if self.permits(scope)]

    def __bool__(self) -> bool:
        return self.unrestricted or bool(self.accounts)

    def describe(self) -> str:
        if self.unrestricted:
            return "all accounts"
        if not self.accounts:
            return "general material only"
        return ", ".join(sorted(self.accounts))
