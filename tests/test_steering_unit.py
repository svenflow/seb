"""Core steering behaviour tests (mocked SDK — no real Claude processes).

Covers: dead session detection, session creation/reuse, concurrency safety,
group sessions, group tier upgrade, HEALME clearing saved IDs, CLAUDE.md
conditional write, and tier-based routing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.conftest import FakeSDKSession


# ---------------------------------------------------------------------------
# Dead session detection
# ---------------------------------------------------------------------------


async def test_dead_session_replaced(backend):
    """A session whose running flag is False should be removed and recreated."""
    await backend.inject_message("signal", "+1111", "+1111", "hi", "admin")
    old_session = backend._sessions["signal:+1111"]
    assert old_session.is_alive()

    # Simulate the session dying
    old_session.running = False

    await backend.inject_message("signal", "+1111", "+1111", "hello again", "admin")
    new_session = backend._sessions["signal:+1111"]

    assert new_session is not old_session
    assert new_session.is_alive()
    assert old_session.stopped  # old session's stop() was called


# ---------------------------------------------------------------------------
# Session creation & reuse
# ---------------------------------------------------------------------------


async def test_first_message_creates_session(backend):
    """First message for a contact creates a new session."""
    assert len(backend._sessions) == 0
    await backend.inject_message("signal", "+2222", "+2222", "hey", "trusted")
    assert "signal:+2222" in backend._sessions
    session = backend._sessions["signal:+2222"]
    assert session.started
    assert session.tier == "trusted"


async def test_second_message_reuses_session(backend):
    """Second message for same contact reuses the existing session."""
    await backend.inject_message("signal", "+3333", "+3333", "msg1", "admin")
    first = backend._sessions["signal:+3333"]

    await backend.inject_message("signal", "+3333", "+3333", "msg2", "admin")
    second = backend._sessions["signal:+3333"]

    assert first is second
    assert len(first.injected) == 2


# ---------------------------------------------------------------------------
# Concurrency safety (asyncio.Lock)
# ---------------------------------------------------------------------------


async def test_concurrent_inject_creates_single_session(backend):
    """Two near-simultaneous inject_message calls for the same new contact
    must produce exactly one session thanks to the asyncio.Lock."""
    await asyncio.gather(
        backend.inject_message("signal", "+4444", "+4444", "a", "admin"),
        backend.inject_message("signal", "+4444", "+4444", "b", "admin"),
    )
    assert "signal:+4444" in backend._sessions
    session = backend._sessions["signal:+4444"]
    # Both messages should have been injected into the same session
    assert len(session.injected) == 2


# ---------------------------------------------------------------------------
# Group sessions
# ---------------------------------------------------------------------------


async def test_group_session_keyed_by_chat_id(backend):
    """Group sessions should be keyed by group chat_id, not the sender."""
    await backend.inject_group_message(
        "signal", "+5555", "group:abc123", "hello group", "trusted"
    )
    # Key should use chat_id, not sender_id
    assert "signal:group:abc123" in backend._sessions
    assert "signal:+5555" not in backend._sessions


async def test_group_session_shared_by_senders(backend):
    """Different senders in the same group share one session."""
    await backend.inject_group_message(
        "signal", "+5555", "group:shared", "msg1", "trusted"
    )
    await backend.inject_group_message(
        "signal", "+6666", "group:shared", "msg2", "trusted"
    )
    assert len([k for k in backend._sessions if "shared" in k]) == 1
    session = backend._sessions["signal:group:shared"]
    assert len(session.injected) == 2


# ---------------------------------------------------------------------------
# Group tier upgrade
# ---------------------------------------------------------------------------


async def test_group_tier_upgrade(backend):
    """If a higher-tier user messages, the group session is recreated."""
    await backend.inject_group_message(
        "signal", "+7777", "group:upgrade", "first", "trusted"
    )
    old = backend._sessions["signal:group:upgrade"]
    assert old.tier == "trusted"

    # Admin arrives → should recreate session with admin tier
    await backend.inject_group_message(
        "signal", "+8888", "group:upgrade", "admin here", "admin"
    )
    new = backend._sessions["signal:group:upgrade"]
    assert new is not old
    assert new.tier == "admin"
    assert old.stopped


async def test_group_no_downgrade(backend):
    """A lower-tier message should NOT downgrade an existing group session."""
    await backend.inject_group_message(
        "signal", "+8888", "group:nodown", "admin msg", "admin"
    )
    admin_session = backend._sessions["signal:group:nodown"]

    await backend.inject_group_message(
        "signal", "+7777", "group:nodown", "trusted msg", "trusted"
    )
    assert backend._sessions["signal:group:nodown"] is admin_session


# ---------------------------------------------------------------------------
# HEALME clears saved session IDs
# ---------------------------------------------------------------------------


async def test_clear_saved_session_ids(backend, patch_sdk_session):
    """clear_saved_session_ids should remove the sessions.json file."""
    import seb.sdk_backend as bmod

    # Create a session so save has something to write
    await backend.inject_message("signal", "+9999", "+9999", "hi", "admin")
    backend.save_session_ids()
    assert bmod.SESSIONS_FILE.exists()

    backend.clear_saved_session_ids()
    assert not bmod.SESSIONS_FILE.exists()
    assert backend._saved_ids == {}


# ---------------------------------------------------------------------------
# CLAUDE.md conditional write
# ---------------------------------------------------------------------------


async def test_claude_md_written_for_fresh_session(backend, patch_sdk_session):
    """CLAUDE.md should be created for a brand-new session."""
    import seb.sdk_backend as bmod

    await backend.inject_message("signal", "+1010", "+1010", "hi", "admin")
    claude_md = bmod.TRANSCRIPTS_DIR / "signal" / "_1010" / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "seb" in content.lower()


async def test_claude_md_not_overwritten_on_resume(backend, patch_sdk_session):
    """When resuming a session (saved_id exists), CLAUDE.md should NOT be
    overwritten if it already exists on disk."""
    import seb.sdk_backend as bmod

    # Pre-populate saved_ids to simulate a resume scenario
    backend._saved_ids["signal:+1111"] = "saved-session-id-123"

    # Write a CLAUDE.md with custom content to detect overwrites
    transcript_dir = bmod.TRANSCRIPTS_DIR / "signal" / "_1111"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    claude_md = transcript_dir / "CLAUDE.md"
    claude_md.write_text("CUSTOM CONTENT — should NOT be replaced")

    await backend.inject_message("signal", "+1111", "+1111", "hi", "admin")
    assert claude_md.read_text() == "CUSTOM CONTENT — should NOT be replaced"


# ---------------------------------------------------------------------------
# Tier routing
# ---------------------------------------------------------------------------


async def test_admin_gets_full_tools(backend):
    """Admin session should be created with tier='admin'."""
    await backend.inject_message("signal", "+admin", "+admin", "hi", "admin")
    session = backend._sessions["signal:+admin"]
    assert session.tier == "admin"


async def test_trusted_gets_restricted(backend):
    """Trusted session should be created with tier='trusted'."""
    await backend.inject_message("signal", "+trusted", "+trusted", "hi", "trusted")
    session = backend._sessions["signal:+trusted"]
    assert session.tier == "trusted"


async def test_default_tier_gets_restricted(backend):
    """Default tier session should be created with tier='default'."""
    await backend.inject_message("signal", "+def", "+def", "hi", "default")
    session = backend._sessions["signal:+def"]
    assert session.tier == "default"


# ---------------------------------------------------------------------------
# Message wrapping
# ---------------------------------------------------------------------------


async def test_dm_message_wrapped_with_metadata(backend):
    """DM messages should be wrapped in <message> tags with metadata."""
    await backend.inject_message("signal", "+wrap", "+wrap", "hello world", "admin")
    session = backend._sessions["signal:+wrap"]
    injected = session.injected[0]
    assert "<message" in injected
    assert "platform='signal'" in injected
    assert "sender='+wrap'" in injected
    assert "hello world" in injected


async def test_group_message_wrapped_with_type(backend):
    """Group messages should include type='group' in the wrapper."""
    await backend.inject_group_message(
        "signal", "+sender", "group:grp1", "group msg", "admin"
    )
    session = backend._sessions["signal:group:grp1"]
    injected = session.injected[0]
    assert "type='group'" in injected
    assert "sender='+sender'" in injected


# ---------------------------------------------------------------------------
# Dead group session detection
# ---------------------------------------------------------------------------


async def test_dead_group_session_replaced(backend):
    """A dead group session should be removed and recreated."""
    await backend.inject_group_message(
        "signal", "+a", "group:dead", "msg1", "admin"
    )
    old = backend._sessions["signal:group:dead"]
    old.running = False

    await backend.inject_group_message(
        "signal", "+b", "group:dead", "msg2", "admin"
    )
    new = backend._sessions["signal:group:dead"]
    assert new is not old
    assert new.is_alive()
    assert old.stopped
