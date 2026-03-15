"""Tests for the pending queries counter in SDKSession.

The counter tracks how many queries are in-flight. It increments on query
dispatch in _run_loop and resets to 0 (not decrements) on ResultMessage
in _receive_loop, because the SDK merges multiple queued queries into a
single turn.

Note: The reset logic lives in _receive_loop (not _handle_message).
_handle_message only handles logging and session_id/turn tracking.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_sdk import ResultMessage, SystemMessage
from seb.sdk_session import SDKSession


def _make_session(**kwargs) -> SDKSession:
    """Create an SDKSession without connecting to anything."""
    defaults = dict(
        session_key="test:+1234",
        tier="admin",
        cwd="/tmp",
        model="haiku",
    )
    defaults.update(kwargs)
    return SDKSession(**defaults)


def _make_result(num_turns=1, session_id="sid-123", duration_ms=100, is_error=False):
    """Create a real ResultMessage instance."""
    return ResultMessage(
        subtype="result",
        duration_ms=duration_ms,
        duration_api_ms=duration_ms,
        is_error=is_error,
        num_turns=num_turns,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Counter basics
# ---------------------------------------------------------------------------


def test_counter_starts_at_zero():
    session = _make_session()
    assert session._pending_queries == 0


def test_is_alive_false_before_start():
    session = _make_session()
    assert not session.is_alive()


# ---------------------------------------------------------------------------
# Counter behaviour — the reset happens in _receive_loop, not _handle_message
# ---------------------------------------------------------------------------


async def test_result_message_resets_counter_in_receive_loop():
    """ResultMessage in _receive_loop resets _pending_queries to 0."""
    session = _make_session()
    session._pending_queries = 5
    session.running = True

    result = _make_result()

    async def fake_receive():
        yield result

    mock_client = MagicMock()
    mock_client.receive_messages.return_value = fake_receive()
    session._client = mock_client

    # Run the receive loop — it will process the ResultMessage then end
    await session._receive_loop()

    assert session._pending_queries == 0


async def test_sequential_counter_via_receive_loop():
    """Simulate: pending=1 -> ResultMessage -> pending=0."""
    session = _make_session()
    session._pending_queries = 1
    session.running = True

    async def fake_receive():
        yield _make_result()

    mock_client = MagicMock()
    mock_client.receive_messages.return_value = fake_receive()
    session._client = mock_client

    await session._receive_loop()
    assert session._pending_queries == 0


async def test_merged_turns_reset_via_receive_loop():
    """Multiple pending queries reset to 0 by a single ResultMessage."""
    session = _make_session()
    session._pending_queries = 3
    session.running = True

    async def fake_receive():
        yield _make_result(num_turns=3)

    mock_client = MagicMock()
    mock_client.receive_messages.return_value = fake_receive()
    session._client = mock_client

    await session._receive_loop()
    assert session._pending_queries == 0


# ---------------------------------------------------------------------------
# Counter increment happens in _run_loop on query dispatch
# ---------------------------------------------------------------------------


async def test_pending_increments_on_query():
    """_pending_queries increments when a query is dispatched in _run_loop."""
    session = _make_session()
    session.running = True

    # Track the value of _pending_queries at query time
    captured_pending = []

    async def capture_query(msg):
        captured_pending.append(session._pending_queries)

    mock_client = MagicMock()
    mock_client.query = capture_query

    receiver_event = asyncio.Event()

    async def hanging_receive():
        await receiver_event.wait()
        return
        yield  # noqa

    mock_client.receive_messages.return_value = hanging_receive()
    session._client = mock_client

    await session._queue.put("msg1")
    await session._queue.put("__SHUTDOWN__")

    try:
        await asyncio.wait_for(session._run_loop(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    # At query time, _pending_queries should have been 1
    assert captured_pending == [1]


# ---------------------------------------------------------------------------
# Session ID capture from _handle_message (ResultMessage)
# ---------------------------------------------------------------------------


def test_session_id_captured_from_result():
    """ResultMessage with a session_id should update the session's _session_id."""
    session = _make_session()
    session._session_id = None

    session._handle_message(_make_result(session_id="new-sid-456"))
    assert session._session_id == "new-sid-456"


# ---------------------------------------------------------------------------
# Session ID captured from _handle_message (SystemMessage)
# ---------------------------------------------------------------------------


def test_session_id_from_system_message():
    """SystemMessage with session_id in data should set _session_id if not already set."""
    session = _make_session()
    session._session_id = None

    msg = SystemMessage(subtype="init", data={"session_id": "sys-sid-789"})
    session._handle_message(msg)
    assert session._session_id == "sys-sid-789"


def test_session_id_not_overwritten_by_system_message():
    """If _session_id is already set, SystemMessage should NOT overwrite it."""
    session = _make_session()
    session._session_id = "existing-id"

    msg = SystemMessage(subtype="init", data={"session_id": "should-not-replace"})
    session._handle_message(msg)
    assert session._session_id == "existing-id"


# ---------------------------------------------------------------------------
# Turn count tracking via _handle_message
# ---------------------------------------------------------------------------


def test_turn_count_incremented_on_result():
    """ResultMessage should increment turn_count by num_turns."""
    session = _make_session()
    assert session.turn_count == 0

    session._handle_message(_make_result(num_turns=3))
    assert session.turn_count == 3

    session._handle_message(_make_result(num_turns=2))
    assert session.turn_count == 5


# ---------------------------------------------------------------------------
# Error count reset via _receive_loop
# ---------------------------------------------------------------------------


async def test_error_count_resets_on_result_in_receive_loop():
    """ResultMessage in _receive_loop should reset _error_count to 0."""
    session = _make_session()
    session._error_count = 2
    session.running = True

    async def fake_receive():
        yield _make_result()

    mock_client = MagicMock()
    mock_client.receive_messages.return_value = fake_receive()
    session._client = mock_client

    await session._receive_loop()
    assert session._error_count == 0
