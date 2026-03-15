"""Manager message routing tests.

Covers: unknown tier dropped, admin command interception,
regular messages forwarded, group vs DM routing.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from seb.manager import Manager


def _make_manager(config_mock=None):
    """Create a Manager with a mocked backend and config."""
    backend = MagicMock()
    backend.inject_message = AsyncMock()
    backend.inject_group_message = AsyncMock()
    backend.stop_all = AsyncMock()
    backend.clear_saved_session_ids = MagicMock()
    manager = Manager(backend)
    return manager, backend


# ---------------------------------------------------------------------------
# Unknown tier messages are dropped
# ---------------------------------------------------------------------------


async def test_unknown_tier_dropped():
    """Messages from unknown contacts should be silently dropped."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "unknown"
        await manager.on_message("signal", "+9999", "+9999", "hi there")

    backend.inject_message.assert_not_called()
    backend.inject_group_message.assert_not_called()


async def test_unknown_telegram_dropped():
    """Unknown Telegram contacts should also be dropped."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_telegram.return_value = "unknown"
        await manager.on_message("telegram", "999999", "999999", "hello")

    backend.inject_message.assert_not_called()


# ---------------------------------------------------------------------------
# Admin commands intercepted (DMs only)
# ---------------------------------------------------------------------------


async def test_healme_intercepted():
    """HEALME command from admin should call stop_all + clear_saved_session_ids."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+admin", "+admin", "HEALME")

    backend.stop_all.assert_awaited_once()
    backend.clear_saved_session_ids.assert_called_once()
    backend.inject_message.assert_not_called()


async def test_restart_intercepted():
    """RESTART command from admin should call stop_all and sys.exit(0)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "RESTART")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()


async def test_admin_command_case_insensitive():
    """Admin commands should be case-insensitive (the code uppercases)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+admin", "+admin", "healme")

    backend.stop_all.assert_awaited_once()
    backend.clear_saved_session_ids.assert_called_once()


async def test_admin_command_with_whitespace():
    """Admin commands with leading/trailing whitespace should still be caught."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+admin", "+admin", "  HEALME  ")

    backend.stop_all.assert_awaited_once()


async def test_admin_command_ignored_in_group():
    """Admin commands in group chats should NOT be intercepted — they go to the session."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+admin", "group:abc", "HEALME")

    # Should be forwarded as a group message, not intercepted
    backend.inject_group_message.assert_awaited_once()
    backend.stop_all.assert_not_awaited()


async def test_non_admin_cannot_use_admin_commands():
    """Trusted contacts sending HEALME should have it forwarded, not intercepted."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        await manager.on_message("signal", "+trusted", "+trusted", "HEALME")

    # Forwarded as regular DM
    backend.inject_message.assert_awaited_once()
    backend.stop_all.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regular messages forwarded to backend
# ---------------------------------------------------------------------------


async def test_dm_forwarded_to_backend():
    """Regular DMs should be forwarded via inject_message."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+1234", "+1234", "hello seb")

    backend.inject_message.assert_awaited_once_with(
        platform="signal",
        sender_id="+1234",
        chat_id="+1234",
        text="hello seb",
        tier="admin",
    )


async def test_trusted_dm_forwarded():
    """Trusted DMs should also be forwarded with correct tier."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        await manager.on_message("signal", "+5678", "+5678", "what's up")

    backend.inject_message.assert_awaited_once_with(
        platform="signal",
        sender_id="+5678",
        chat_id="+5678",
        text="what's up",
        tier="trusted",
    )


# ---------------------------------------------------------------------------
# Group vs DM routing
# ---------------------------------------------------------------------------


async def test_group_message_routed_to_inject_group():
    """Messages with chat_id starting with 'group:' go to inject_group_message."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+sender", "group:mygroup", "hey team")

    backend.inject_group_message.assert_awaited_once_with(
        platform="signal",
        sender_id="+sender",
        chat_id="group:mygroup",
        text="hey team",
        tier="admin",
    )
    backend.inject_message.assert_not_awaited()


async def test_dm_not_routed_to_group():
    """DM messages should NOT go to inject_group_message."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        await manager.on_message("signal", "+sender", "+sender", "dm text")

    backend.inject_message.assert_awaited_once()
    backend.inject_group_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Platform handling
# ---------------------------------------------------------------------------


async def test_unknown_platform_dropped():
    """Messages from unknown platforms should be dropped."""
    manager, backend = _make_manager()

    await manager.on_message("whatsapp", "+1111", "+1111", "hello")

    backend.inject_message.assert_not_called()
    backend.inject_group_message.assert_not_called()


async def test_telegram_tier_lookup():
    """Telegram messages should use tier_for_telegram with int conversion."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_telegram.return_value = "trusted"
        await manager.on_message("telegram", "123456", "123456", "hi from tg")

    mock_cfg.return_value.tier_for_telegram.assert_called_once_with(123456)
    backend.inject_message.assert_awaited_once()
