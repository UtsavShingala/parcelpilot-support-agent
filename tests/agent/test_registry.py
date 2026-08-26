"""What a role is offered is a hint; what dispatch runs is the control."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.agent.registry import ToolRegistry, build_registry
from parcelpilot.agent.tools.base import Tool, object_schema, string_field
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

CUSTOMER = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")
SUPPORT = CallerContext(role=Role.SUPPORT_AGENT, display_name="Maya")
OPS = CallerContext(role=Role.OPS_MANAGER, display_name="Ops")

MUTATING_INTERNAL_TOOLS = {"prepare_ticket_update", "prepare_follow_up"}


@pytest.fixture(scope="module")
def registry(corpus_dir: Path) -> Iterator[ToolRegistry]:
    data = OperationalData.open()
    yield build_registry(DocumentStore.from_settings(), data)
    data.close()


def test_confirmation_is_not_a_tool(registry: ToolRegistry) -> None:
    """The one mutator must be unreachable from the model, by construction."""
    names = [tool.name for tool in registry.tools]
    assert not any("confirm" in name for name in names)


def test_a_customer_is_never_offered_ticket_mutating_tools(registry: ToolRegistry) -> None:
    offered = set(registry.names_for(Role.CUSTOMER))
    assert not offered & MUTATING_INTERNAL_TOOLS
    assert "prepare_escalation" in offered, "a customer must still be able to ask for a human"


def test_internal_roles_are_offered_the_full_set(registry: ToolRegistry) -> None:
    for role in (Role.SUPPORT_AGENT, Role.OPS_MANAGER):
        assert MUTATING_INTERNAL_TOOLS <= set(registry.names_for(role))


def test_everyone_gets_the_read_tools(registry: ToolRegistry) -> None:
    expected = {"search_documents", "lookup_orders", "lookup_tickets", "calculate"}
    for role in Role:
        assert expected <= set(registry.names_for(role))


def test_dispatch_refuses_a_tool_the_role_was_never_offered(registry: ToolRegistry) -> None:
    """A model can emit a call it was never shown; the schema list does not stop it."""
    assert "prepare_ticket_update" not in registry.names_for(Role.CUSTOMER)

    result = registry.dispatch(
        "prepare_ticket_update", {"ticket_id": "TKT-501", "status": "closed"}, CUSTOMER
    )
    assert not result.ok
    assert "may not use" in (result.error or "")
    assert "escalation" in (result.error or "")


def test_dispatch_reports_an_unknown_tool_with_the_real_list(registry: ToolRegistry) -> None:
    result = registry.dispatch("delete_everything", {}, SUPPORT)
    assert not result.ok
    assert "no tool called" in (result.error or "")
    assert "search_documents" in (result.error or "")


def test_dispatch_refuses_arguments_the_tool_does_not_take(registry: ToolRegistry) -> None:
    """A hallucinated argument must fail loudly, not be silently dropped."""
    result = registry.dispatch(
        "lookup_orders", {"order_id": "ORD-1001", "include_deleted": True}, SUPPORT
    )
    assert not result.ok
    assert "include_deleted" in (result.error or "")


def test_a_tool_error_comes_back_as_a_result_not_an_exception(
    registry: ToolRegistry,
) -> None:
    result = registry.dispatch(
        "calculate", {"operation": "pickup_delay", "order_id": "ORD-1001"}, SUPPORT
    )
    assert not result.ok
    assert "threshold_hours" in (result.error or "")


def test_a_successful_call_carries_its_payload(registry: ToolRegistry) -> None:
    result = registry.dispatch("search_documents", {"query": "cancellation fee"}, CUSTOMER)
    assert result.ok
    assert result.payload["result_count"] > 0
    assert not result.mutating


def test_prepare_tools_are_marked_mutating(registry: ToolRegistry) -> None:
    """The interface needs to distinguish a lookup from something awaiting a yes."""
    result = registry.dispatch(
        "prepare_escalation", {"reason": "needs a human decision"}, SUPPORT
    )
    assert result.ok
    assert result.mutating


def test_scoping_survives_dispatch(registry: ToolRegistry) -> None:
    other = registry.dispatch("lookup_orders", {"order_id": "ORD-2001"}, CUSTOMER)
    assert other.ok
    assert other.payload["result_count"] == 0


def test_results_serialise_for_the_model(registry: ToolRegistry) -> None:
    ok = registry.dispatch("lookup_account", {}, CUSTOMER)
    failed = registry.dispatch("nope", {}, CUSTOMER)

    assert "ACCT-001" in ok.to_message_content()
    assert "error" in failed.to_message_content()


def test_schemas_are_shaped_for_the_chat_completions_api(registry: ToolRegistry) -> None:
    for schema in registry.schemas_for(Role.OPS_MANAGER):
        assert schema["type"] == "function"
        function = schema["function"]
        assert set(function) >= {"name", "description", "parameters"}
        assert function["parameters"]["additionalProperties"] is False


def test_duplicate_tool_names_are_rejected() -> None:
    def handler(caller: CallerContext) -> None:  # pragma: no cover - never called
        return None

    tool = Tool(
        name="same",
        description="",
        parameters=object_schema({"x": string_field("x")}),
        handler=handler,
    )
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry((tool, tool))
