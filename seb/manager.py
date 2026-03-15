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

    # Drop unknown contacts silently
    if tier == "unknown":
      logger.info("Dropping message from unknown %s:%s", platform, sender_id)
      return

    # Intercept admin commands
    is_group = chat_id.startswith("group:")
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
      await self._backend.inject_group_message(
        platform=platform,
        sender_id=sender_id,
        chat_id=chat_id,
        text=text,
        tier=tier,
      )
    else:
      await self._backend.inject_message(
        platform=platform,
        sender_id=sender_id,
        chat_id=chat_id,
        text=text,
        tier=tier,
      )

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
