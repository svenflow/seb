"""Routing layer — contact tier lookups, admin commands, session dispatch."""

from __future__ import annotations

import logging

from seb.config import get_config
from seb.sdk_backend import SDKBackend
from seb.sdk_session import QueuedMessage

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

    # Tier lookup
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
    stripped = text.strip().upper()
    if tier == "admin" and stripped in ADMIN_COMMANDS:
      await self._handle_admin_command(stripped, platform, sender_id)
      return

    msg = QueuedMessage(
      platform=platform,
      sender_id=sender_id,
      chat_id=chat_id,
      text=text,
      tier=tier,
    )
    await self._backend.inject_message(msg)

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
      # Placeholder — could clear session state, reload config, etc.
      logger.info("HEALME: resetting sessions")
      await self._backend.stop_all()
