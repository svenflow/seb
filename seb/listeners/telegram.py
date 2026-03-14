import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from seb.config import get_config

logger = logging.getLogger(__name__)

# Normalized message passed to the manager
type MessageCallback = Callable[[str, str, str, str], Coroutine[Any, Any, None]]
# (platform, sender_id, chat_id, text)


class TelegramListener:
  """Polls Telegram for messages and dispatches to the manager callback."""

  def __init__(self, on_message: MessageCallback) -> None:
    self._on_message = on_message
    cfg = get_config()
    self._app = ApplicationBuilder().token(cfg.telegram_bot_token).build()
    self._app.add_handler(
      MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle)
    )

  async def _handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
      return

    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id) if update.effective_chat else user_id
    text = update.message.text or ""

    logger.debug("telegram message from %s: %r", user_id, text[:80])
    await self._on_message("telegram", user_id, chat_id, text)

  async def send(self, chat_id: str, text: str) -> None:
    """Send a message back to a Telegram chat."""
    bot = self._app.bot
    # Telegram limit is 4096 chars per message
    for chunk in _chunk(text, 4096):
      await bot.send_message(chat_id=int(chat_id), text=chunk)

  async def run(self) -> None:
    """Start polling — runs until cancelled."""
    logger.info("Starting Telegram listener (long polling)")
    await self._app.initialize()
    await self._app.start()
    await self._app.updater.start_polling(drop_pending_updates=True)
    # Block until cancelled
    try:
      await asyncio.Event().wait()
    finally:
      await self._app.updater.stop()
      await self._app.stop()
      await self._app.shutdown()


def _chunk(text: str, size: int) -> list[str]:
  return [text[i : i + size] for i in range(0, len(text), size)]
