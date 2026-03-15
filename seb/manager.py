"""Routing layer — contact tier lookups, admin commands, session dispatch."""

from __future__ import annotations

import logging
import re

from collections.abc import Callable, Coroutine
from typing import Any

from seb.config import get_config
from seb.sdk_backend import SDKBackend

type ReplyFunction = Callable[[str, str, str], Coroutine[Any, Any, None]]

logger = logging.getLogger(__name__)

ADMIN_COMMANDS = {"HEALME", "RESTART", "REBOOT"}

# Pattern to match "seb <command>" in group chats (case-insensitive)
_GROUP_COMMAND_RE = re.compile(r"^seb\s+(\w+)\s*$", re.IGNORECASE)


class Manager:
  """Routes incoming messages to the right session or handles admin commands."""

  def __init__(self, backend: SDKBackend) -> None:
    self._backend = backend
    self._reply_fn: ReplyFunction | None = None

  def set_reply_fn(self, fn: ReplyFunction) -> None:
    """Set callback for sending replies from admin command handlers."""
    self._reply_fn = fn

  async def on_message(
    self, platform: str, sender_id: str, chat_id: str, text: str
  ) -> None:
    cfg = get_config()

    # Tier lookup (based on sender, not chat)
    if platform == "telegram":
      try:
        tier = cfg.tier_for_telegram(int(sender_id))
      except ValueError:
        tier = "unknown"
    elif platform == "signal":
      tier = cfg.tier_for_signal(sender_id)
    else:
      logger.warning("Unknown platform: %s", platform)
      return

    # Intercept admin commands
    is_group = chat_id.startswith("group:")

    # Drop unknown contacts in DMs; in groups, treat them as "default" tier
    # since they're a member of the group (Signal requires invites to join).
    if tier == "unknown":
      if is_group:
        logger.info("Unknown sender %s in group %s, routing as default tier", sender_id, chat_id)
        tier = "default"
      else:
        logger.info("Dropping DM from unknown %s:%s", platform, sender_id)
        return
    stripped = text.strip().upper()

    # In DMs: bare command (e.g. "RESTART")
    # In groups: "seb <command>" (e.g. "seb reboot")
    admin_command = None
    if tier == "admin":
      if not is_group and stripped in ADMIN_COMMANDS:
        admin_command = stripped
      elif is_group:
        m = _GROUP_COMMAND_RE.match(text.strip())
        if m and m.group(1).upper() in ADMIN_COMMANDS:
          admin_command = m.group(1).upper()

    if admin_command:
      await self._handle_admin_command(admin_command, platform, sender_id, chat_id)
      return

    if is_group:
      if not self._is_relevant_group_message(text, tier):
        logger.info("Dropping irrelevant group message from %s (tier=%s): %r", sender_id, tier, text[:50])
        return
      logger.info("Routing group message from %s (tier=%s) to %s", sender_id, tier, chat_id)
      await self._backend.inject_group_message(
        platform=platform,
        sender_id=sender_id,
        chat_id=chat_id,
        text=text,
        tier=tier,
      )
    else:
      logger.info("Routing DM from %s (tier=%s)", sender_id, tier)
      await self._backend.inject_message(
        platform=platform,
        sender_id=sender_id,
        chat_id=chat_id,
        text=text,
        tier=tier,
      )

  def _is_relevant_group_message(self, text: str, tier: str) -> bool:
    """Check whether a group message is relevant to this bot.

    Returns True if the message should be routed to the bot's session:
    1. Message mentions the bot's name anywhere → always route
    2. Message starts with another known name (word boundary) → always drop
    3. Sender is admin and no specific name addressing → route
    4. Otherwise → drop
    """
    cfg = get_config()
    bot_name = cfg.bot_name
    # Word-boundary match for the bot's name anywhere in the text
    bot_pattern = re.compile(rf"\b{re.escape(bot_name)}\b", re.IGNORECASE)

    # 1. Message mentions the bot anywhere → relevant
    if bot_pattern.search(text):
      return True

    # 2. Message starts with another known name → directed at someone else, drop
    stripped = text.strip()
    first_word = stripped.split()[0].lower() if stripped else ""
    # Strip trailing punctuation (comma, colon) from first word for matching
    first_word_clean = first_word.rstrip(",:")
    if first_word_clean and first_word_clean in cfg.other_names:
      return False

    # 3. Admin catch-all: route generic messages
    if tier == "admin":
      return True

    # 4. Non-admin, no bot mention → irrelevant
    return False

  async def _reply(self, platform: str, chat_id: str, text: str) -> None:
    """Send a reply via the registered reply function, if available."""
    if self._reply_fn:
      try:
        await self._reply_fn(platform, chat_id, text)
      except Exception as e:
        logger.warning("Failed to send admin command reply: %s", e)

  async def _handle_admin_command(
    self, command: str, platform: str, sender_id: str, chat_id: str
  ) -> None:
    logger.info("Admin command %r from %s:%s", command, platform, sender_id)
    if command in ("RESTART", "REBOOT"):
      # Save sessions and exit; systemd (Restart=on-failure) brings us back.
      # REBOOT and RESTART are identical — both rely on systemd, no separate
      # systemctl call needed (avoids overlapping restart race).
      await self._reply(platform, chat_id, "Rebooting… be back in ~10s.")
      await self._backend.stop_all()
      import sys
      sys.exit(0)
    elif command == "HEALME":
      # Full reset: stop all sessions, clear saved IDs, then exit so systemd
      # restarts cleanly (avoids half-reset state).
      logger.info("HEALME: resetting sessions and clearing saved IDs")
      await self._reply(platform, chat_id, "Healing… resetting all sessions.")
      await self._backend.stop_all()
      self._backend.clear_saved_session_ids()
      import sys
      sys.exit(0)
