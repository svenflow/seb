"""Routing layer — contact tier lookups, admin commands, session dispatch."""

from __future__ import annotations

import logging

from seb.config import get_config
from seb.sdk_backend import SDKBackend

logger = logging.getLogger(__name__)

ADMIN_COMMANDS = {"HEALME", "RESTART"}


class Manager:
  """Routes incoming messages to the right session or handles admin commands."""

  def __init__(self, backend: SDKBackend) -> None:
    self._backend = backend

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

    # Intercept admin commands (DMs only)
    is_group = chat_id.startswith("group:")
    stripped = text.strip().upper()
    if not is_group and tier == "admin" and stripped in ADMIN_COMMANDS:
      await self._handle_admin_command(stripped, platform, sender_id)
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

  async def _handle_admin_command(
    self, command: str, platform: str, sender_id: str
  ) -> None:
    logger.info("Admin command %r from %s:%s", command, platform, sender_id)
    if command == "RESTART":
      # Save sessions and exit; systemd will restart the process
      await self._backend.stop_all()
      import sys
      sys.exit(0)
    elif command == "HEALME":
      # Full reset: stop all sessions, then clear saved IDs so they aren't
      # resumed with potentially broken state on next message.
      logger.info("HEALME: resetting sessions and clearing saved IDs")
      await self._backend.stop_all()
      self._backend.clear_saved_session_ids()
