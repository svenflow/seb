"""seb — entry point. Starts all listeners and the session backend."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from seb.config import get_config
from seb.listeners.signal import SignalListener
from seb.listeners.telegram import TelegramListener
from seb.manager import Manager
from seb.sdk_backend import SDKBackend

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_telegram_listener: TelegramListener | None = None
_signal_listener: SignalListener | None = None


async def run() -> None:
  global _telegram_listener, _signal_listener

  cfg = get_config()

  # Agent SDK backend — no API key needed, uses OAuth via `claude login`
  backend = SDKBackend(
    model=cfg.claude_model,
    cli_path=cfg.claude_cli_path,
  )
  manager = Manager(backend)

  _signal_listener = SignalListener(
    base_url=cfg.signal_api_url,
    our_number=cfg.signal_number,
    on_message=manager.on_message,
  )

  tasks = [asyncio.create_task(_signal_listener.run(), name="signal")]

  if cfg.telegram_bot_token and not cfg.telegram_bot_token.startswith("123456789"):
    _telegram_listener = TelegramListener(on_message=manager.on_message)
    tasks.append(asyncio.create_task(_telegram_listener.run(), name="telegram"))
    logger.info("Telegram listener enabled")
  else:
    logger.info("Telegram listener disabled (no token configured)")

  loop = asyncio.get_running_loop()

  def _shutdown() -> None:
    logger.info("Shutdown signal received")
    for task in asyncio.all_tasks(loop):
      task.cancel()

  loop.add_signal_handler(signal.SIGINT, _shutdown)
  loop.add_signal_handler(signal.SIGTERM, _shutdown)

  logger.info("seb starting up (Agent SDK, model=%s)", cfg.claude_model)

  try:
    await asyncio.gather(*tasks)
  except asyncio.CancelledError:
    logger.info("seb shutting down")
  finally:
    await backend.stop_all()
    logger.info("seb stopped")


def main() -> None:
  try:
    asyncio.run(run())
  except KeyboardInterrupt:
    pass
  sys.exit(0)


if __name__ == "__main__":
  main()
