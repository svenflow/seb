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
    """HEALME command from admin should call stop_all + clear_saved_session_ids + sys.exit(0)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "HEALME")

    assert exc_info.value.code == 0
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
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "healme")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()
    backend.clear_saved_session_ids.assert_called_once()


async def test_admin_command_with_whitespace():
    """Admin commands with leading/trailing whitespace should still be caught."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "  HEALME  ")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()


async def test_bare_admin_command_ignored_in_group():
    """Bare admin commands (e.g. 'HEALME') in group chats should NOT be intercepted."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "HEALME")

    # Should be forwarded as a group message, not intercepted
    backend.inject_group_message.assert_awaited_once()
    backend.stop_all.assert_not_awaited()


# ---------------------------------------------------------------------------
# Group admin commands via "seb <command>" pattern
# ---------------------------------------------------------------------------


async def test_seb_healme_in_group():
    """'seb healme' in a group should be intercepted as admin command."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "group:abc", "seb healme")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()
    backend.clear_saved_session_ids.assert_called_once()
    backend.inject_group_message.assert_not_awaited()


async def test_seb_restart_in_group():
    """'seb restart' in a group should be intercepted as admin command."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "group:abc", "seb restart")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()
    backend.inject_group_message.assert_not_awaited()


async def test_seb_reboot_in_group():
    """'seb reboot' in a group should be intercepted as admin command."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "group:abc", "seb reboot")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()
    backend.inject_group_message.assert_not_awaited()


async def test_seb_command_case_insensitive_in_group():
    """'Seb REBOOT' (mixed case) in a group should be intercepted."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "group:abc", "Seb REBOOT")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()


async def test_seb_non_command_in_group_forwarded():
    """'seb hello' in a group should NOT be intercepted (not an admin command)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "seb hello")

    backend.inject_group_message.assert_awaited_once()
    backend.stop_all.assert_not_awaited()


async def test_seb_command_non_admin_in_group_forwarded():
    """'seb restart' from a non-admin in a group should be forwarded, not intercepted."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "group:abc", "seb restart")

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
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
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


# ---------------------------------------------------------------------------
# REBOOT command (identical to RESTART)
# ---------------------------------------------------------------------------


async def test_reboot_intercepted():
    """REBOOT command from admin should call stop_all and sys.exit(0), same as RESTART."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "REBOOT")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()
    # REBOOT should NOT clear saved session IDs (unlike HEALME)
    backend.clear_saved_session_ids.assert_not_called()


async def test_reboot_case_insensitive():
    """REBOOT should work case-insensitively."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "reboot")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()


# ---------------------------------------------------------------------------
# set_reply_fn and confirmation messages
# ---------------------------------------------------------------------------


async def test_set_reply_fn_called_on_restart():
    """When reply_fn is set, RESTART should send a confirmation message."""
    manager, backend = _make_manager()
    reply_fn = AsyncMock()
    manager.set_reply_fn(reply_fn)

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit):
            await manager.on_message("signal", "+admin", "+admin", "RESTART")

    reply_fn.assert_awaited_once()
    call_args = reply_fn.call_args
    assert call_args[0][0] == "signal"  # platform
    assert call_args[0][1] == "+admin"  # chat_id
    assert "reboot" in call_args[0][2].lower() or "back" in call_args[0][2].lower()


async def test_set_reply_fn_called_on_healme():
    """When reply_fn is set, HEALME should send a confirmation message."""
    manager, backend = _make_manager()
    reply_fn = AsyncMock()
    manager.set_reply_fn(reply_fn)

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit):
            await manager.on_message("signal", "+admin", "+admin", "HEALME")

    reply_fn.assert_awaited_once()
    call_args = reply_fn.call_args
    assert call_args[0][0] == "signal"
    assert call_args[0][1] == "+admin"
    assert "heal" in call_args[0][2].lower() or "reset" in call_args[0][2].lower()


async def test_no_reply_fn_does_not_crash():
    """Admin commands should work fine even without a reply_fn set."""
    manager, backend = _make_manager()
    # Don't set reply_fn — it should default to None

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit):
            await manager.on_message("signal", "+admin", "+admin", "RESTART")

    backend.stop_all.assert_awaited_once()


async def test_reply_fn_error_does_not_block_command():
    """If reply_fn raises, the admin command should still execute."""
    manager, backend = _make_manager()
    reply_fn = AsyncMock(side_effect=RuntimeError("send failed"))
    manager.set_reply_fn(reply_fn)

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit) as exc_info:
            await manager.on_message("signal", "+admin", "+admin", "RESTART")

    assert exc_info.value.code == 0
    backend.stop_all.assert_awaited_once()


async def test_reply_fn_receives_group_chat_id():
    """In a group, the reply should go to the group chat_id, not the sender."""
    manager, backend = _make_manager()
    reply_fn = AsyncMock()
    manager.set_reply_fn(reply_fn)

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        with pytest.raises(SystemExit):
            await manager.on_message("signal", "+admin", "group:mygrp", "seb reboot")

    reply_fn.assert_awaited_once()
    call_args = reply_fn.call_args
    assert call_args[0][1] == "group:mygrp"  # chat_id, not sender


# ---------------------------------------------------------------------------
# Group message relevance filtering
# ---------------------------------------------------------------------------


async def test_group_msg_starting_with_seb_routed():
    """'seb do X' should be routed to the bot session."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "group:abc", "seb do the thing")

    backend.inject_group_message.assert_awaited_once()


async def test_group_msg_hey_seb_routed():
    """'hey seb' should be routed to the bot session."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "group:abc", "hey seb can you help")

    backend.inject_group_message.assert_awaited_once()


async def test_group_msg_seb_mid_sentence_routed():
    """Message containing 'seb' mid-sentence should be routed."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message(
            "signal", "+trusted", "group:abc", "can you ask seb to check the server"
        )

    backend.inject_group_message.assert_awaited_once()


async def test_group_msg_sven_do_x_dropped():
    """'sven do X' from non-admin should be dropped (no bot mention)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "group:abc", "sven do the thing")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_random_no_names_dropped():
    """Random message with no names from non-admin should be dropped."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "group:abc", "what time is dinner?")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_admin_no_names_routed():
    """Admin messages without names should be routed."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "what time is dinner?")

    backend.inject_group_message.assert_awaited_once()


async def test_group_msg_admin_starting_with_sven_dropped():
    """Admin messages starting with 'sven' should be dropped (directed at someone else)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "sven, check your email")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_word_boundary_no_false_positive():
    """'sebastian' should NOT match the bot name 'seb' (word boundary check)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "group:abc", "sebastian is great")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_dm_not_filtered():
    """DMs should NOT go through the group relevance filter."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+trusted", "+trusted", "random message no name")

    backend.inject_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# "sven do X" routing fix — other_names word-boundary matching
# ---------------------------------------------------------------------------


async def test_group_msg_sven_do_x_admin_dropped():
    """'sven do X' from admin should be DROPPED (directed at sven, no comma/colon)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "sven do the thing")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_sven_comma_admin_dropped():
    """'sven, do X' from admin should be DROPPED (directed at sven with comma)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "sven, check your email")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_hey_sven_and_seb_routed():
    """'hey sven and seb' should be ROUTED (contains 'seb')."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "trusted"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message(
            "signal", "+trusted", "group:abc", "hey sven and seb check this out"
        )

    backend.inject_group_message.assert_awaited_once()


async def test_group_msg_sven_colon_admin_dropped():
    """'sven: do X' from admin should be DROPPED."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "sven: look at this")

    backend.inject_group_message.assert_not_awaited()


async def test_group_msg_Sven_capitalized_dropped():
    """'Sven do X' (capitalized) from admin should be DROPPED (case-insensitive)."""
    manager, backend = _make_manager()

    with patch("seb.manager.get_config") as mock_cfg:
        mock_cfg.return_value.tier_for_signal.return_value = "admin"
        mock_cfg.return_value.bot_name = "seb"
        mock_cfg.return_value.other_names = {"sven"}
        await manager.on_message("signal", "+admin", "group:abc", "Sven do the thing")

    backend.inject_group_message.assert_not_awaited()
