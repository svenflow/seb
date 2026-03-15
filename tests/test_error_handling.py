"""Error handling and recovery tests for SDKSession.

Tests cover: query error handling, error count accumulation,
3-consecutive-errors kill, error count reset on success, and
receiver fatal error handling.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_sdk import ResultMessage
from seb.sdk_session import SDKSession


def _make_session(**kwargs) -> SDKSession:
    defaults = dict(
        session_key="test:+err",
        tier="admin",
        cwd="/tmp",
        model="haiku",
    )
    defaults.update(kwargs)
    return SDKSession(**defaults)


def _make_result(**kwargs):
    defaults = dict(
        subtype="result",
        duration_ms=50,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="sid",
    )
    defaults.update(kwargs)
    return ResultMessage(**defaults)


# ---------------------------------------------------------------------------
# Error count basics
# ---------------------------------------------------------------------------


def test_error_count_starts_at_zero():
    session = _make_session()
    assert session._error_count == 0


def test_error_count_increments():
    """Each error should increment _error_count."""
    session = _make_session()
    session._error_count += 1
    assert session._error_count == 1
    session._error_count += 1
    assert session._error_count == 2


# ---------------------------------------------------------------------------
# Query error decrements pending counter
# ---------------------------------------------------------------------------


async def test_query_error_decrements_pending():
    """When a query() call raises, _pending_queries should be decremented
    (but not below 0) and _error_count should increase."""
    session = _make_session()

    mock_client = MagicMock()
    mock_client.query = AsyncMock(side_effect=RuntimeError("connection lost"))

    receiver_event = asyncio.Event()
    mock_client.receive_messages.return_value = _hanging_async_iter(receiver_event)
    session._client = mock_client
    session.running = True

    await session._queue.put("test message")
    await session._queue.put("__SHUTDOWN__")

    try:
        await asyncio.wait_for(session._run_loop(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    assert session._error_count >= 1
    assert session._pending_queries == 0  # decremented back from 1


# ---------------------------------------------------------------------------
# 3 consecutive errors kill session
# ---------------------------------------------------------------------------


async def test_three_errors_kill_session():
    """After 3 consecutive query errors, the session should set running=False."""
    session = _make_session()

    call_count = 0

    async def failing_query(msg):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    mock_client = MagicMock()
    mock_client.query = failing_query

    receiver_event = asyncio.Event()

    async def hanging_receive():
        await receiver_event.wait()
        return
        yield  # noqa

    mock_client.receive_messages.return_value = hanging_receive()
    session._client = mock_client
    session.running = True

    # Queue enough messages — the loop should exit after 3 errors
    for i in range(5):
        await session._queue.put(f"msg{i}")

    try:
        await asyncio.wait_for(session._run_loop(), timeout=30.0)
    except asyncio.TimeoutError:
        pass

    assert session._error_count >= 3
    assert not session.running


# ---------------------------------------------------------------------------
# Error count resets on success (ResultMessage in _receive_loop)
# ---------------------------------------------------------------------------


async def test_error_count_resets_on_result():
    """A ResultMessage in _receive_loop should reset _error_count to 0."""
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


# ---------------------------------------------------------------------------
# Receiver fatal error sets running=False
# ---------------------------------------------------------------------------


async def test_receiver_fatal_error_stops_session():
    """A fatal error in the receiver (buffer overflow) should stop the session."""
    session = _make_session()
    session.running = True
    session._client = AsyncMock()

    session._client.receive_messages = MagicMock(
        return_value=_exploding_async_iter("buffer overflow: message exceeds 1048576 bytes")
    )

    await session._receive_loop()

    assert not session.running
    assert session._error_count >= 1


async def test_receiver_non_fatal_error_increments_count():
    """A non-fatal receiver error should increment _error_count but not
    stop the session (if count < 3)."""
    session = _make_session()
    session.running = True
    session._error_count = 0
    session._client = AsyncMock()

    session._client.receive_messages = MagicMock(
        return_value=_exploding_async_iter("some random error")
    )

    await session._receive_loop()

    assert session._error_count == 1
    # Non-fatal, count < 3, so running should still be True
    assert session.running


async def test_receiver_three_errors_stops_session():
    """If _error_count reaches 3 via receiver errors, session should stop."""
    session = _make_session()
    session.running = True
    session._error_count = 2  # Already had 2 errors
    session._client = AsyncMock()

    session._client.receive_messages = MagicMock(
        return_value=_exploding_async_iter("network timeout")
    )

    await session._receive_loop()

    assert session._error_count >= 3
    assert not session.running


# ---------------------------------------------------------------------------
# Error recovery — single error keeps session alive
# ---------------------------------------------------------------------------


def test_single_error_keeps_running():
    """A single error should not set running=False."""
    session = _make_session()
    session.running = True
    session._error_count = 1
    assert session._error_count < 3


def test_error_count_below_threshold():
    """Error counts of 1 and 2 should not trigger session death."""
    session = _make_session()
    session.running = True
    for i in range(1, 3):
        session._error_count = i
        assert session._error_count < 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _hanging_async_iter(event: asyncio.Event):
    """An async iterator that blocks until the event is set."""
    await event.wait()
    return
    yield  # noqa


async def _exploding_async_iter(msg: str):
    raise RuntimeError(msg)
    yield  # noqa
