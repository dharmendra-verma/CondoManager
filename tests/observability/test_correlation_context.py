"""Tests for the CM-27 additions to ``agents.observability.correlation``.

Covers ``tenant_id`` and ``agent_name`` contextvars + ``with_tenant`` /
``with_agent`` scopes. The CM-21 ``request_id`` surface is covered by
``test_correlation.py`` — we don't re-test it here.

Critical assertion: contextvars survive async boundaries. ``asyncio.gather``
and ``asyncio.create_task`` both snapshot the current ``contextvars.Context``,
which is what makes correlation work across the system. If this stops being
true, our entire logging story silently degrades.
"""

from __future__ import annotations

import asyncio

import pytest

from agents.observability import correlation


def test_tenant_id_default_none() -> None:
    """Outside any ``with_tenant`` scope, get_tenant_id returns None."""
    assert correlation.get_tenant_id() is None


def test_agent_name_default_none() -> None:
    """Outside any ``with_agent`` scope, get_agent_name returns None."""
    assert correlation.get_agent_name() is None


def test_with_tenant_scopes_and_resets() -> None:
    assert correlation.get_tenant_id() is None
    with correlation.with_tenant("tenant-42") as yielded:
        assert yielded == "tenant-42"
        assert correlation.get_tenant_id() == "tenant-42"
    assert correlation.get_tenant_id() is None


def test_with_agent_scopes_and_resets() -> None:
    assert correlation.get_agent_name() is None
    with correlation.with_agent("triage") as yielded:
        assert yielded == "triage"
        assert correlation.get_agent_name() == "triage"
    assert correlation.get_agent_name() is None


def test_nested_with_tenant_restores_outer() -> None:
    with correlation.with_tenant("outer"):
        assert correlation.get_tenant_id() == "outer"
        with correlation.with_tenant("inner"):
            assert correlation.get_tenant_id() == "inner"
        assert correlation.get_tenant_id() == "outer"
    assert correlation.get_tenant_id() is None


def test_set_tenant_id_returns_resettable_token() -> None:
    """Direct setter + reset is the API for framework middleware that
    can't use the context-manager pattern (e.g. FastAPI dependency)."""
    token = correlation.set_tenant_id("manual")
    assert correlation.get_tenant_id() == "manual"
    correlation._tenant_id_var.reset(token)
    assert correlation.get_tenant_id() is None


def test_set_agent_name_returns_resettable_token() -> None:
    token = correlation.set_agent_name("manual-agent")
    assert correlation.get_agent_name() == "manual-agent"
    correlation._agent_name_var.reset(token)
    assert correlation.get_agent_name() is None


@pytest.mark.asyncio
async def test_with_tenant_survives_asyncio_gather() -> None:
    """``asyncio.gather`` must propagate the contextvar — every child
    coroutine sees the same tenant_id as the caller scope."""

    async def read_tenant() -> str | None:
        return correlation.get_tenant_id()

    with correlation.with_tenant("gather-tenant"):
        results = await asyncio.gather(read_tenant(), read_tenant(), read_tenant())

    assert results == ["gather-tenant", "gather-tenant", "gather-tenant"]


@pytest.mark.asyncio
async def test_with_agent_survives_create_task() -> None:
    """``asyncio.create_task`` snapshots the current Context, so the
    spawned task sees the agent_name from the spawn site."""

    async def read_agent() -> str | None:
        return correlation.get_agent_name()

    with correlation.with_agent("create-task-agent"):
        task = asyncio.create_task(read_agent())
        result = await task

    assert result == "create-task-agent"


@pytest.mark.asyncio
async def test_sibling_tasks_are_isolated() -> None:
    """Two sibling tasks each set their own tenant_id inside their own
    scope; neither leaks into the other. This is the property that lets
    multi-tenant request handling not crosstalk."""

    async def work(tenant: str, seen: list[str | None]) -> None:
        with correlation.with_tenant(tenant):
            # Yield to the scheduler so the other task gets a chance.
            await asyncio.sleep(0)
            seen.append(correlation.get_tenant_id())

    seen_a: list[str | None] = []
    seen_b: list[str | None] = []
    await asyncio.gather(work("tenant-a", seen_a), work("tenant-b", seen_b))

    assert seen_a == ["tenant-a"]
    assert seen_b == ["tenant-b"]
    # Caller scope is also restored.
    assert correlation.get_tenant_id() is None
