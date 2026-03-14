"""Signal listener via bbernhard/signal-cli-rest-api (HTTP + WebSocket)."""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import httpx

logger = logging.getLogger(__name__)

type MessageCallback = Callable[[str, str, str, str], Coroutine[Any, Any, None]]
# (platform, sender_id, chat_id, text)


class SignalListener:
  """
  Connects to signal-cli-rest-api for send/receive.

  Receives via WebSocket: ws://<host>/v1/receive/<number>
  Sends via HTTP POST:    http://<host>/v2/send
  """

  def __init__(self, base_url: str, our_number: str, on_message: MessageCallback) -> None:
    # base_url e.g. "http://127.0.0.1:6001"
    self._base_url = base_url.rstrip("/")
    self._ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
    self._our_number = our_number
    self._on_message = on_message

  async def send(self, recipient: str, text: str) -> None:
    """Send a Signal message via the REST API."""
    url = f"{self._base_url}/v2/send"
    payload = {
      "number": self._our_number,
      "recipients": [recipient],
      "message": text,
    }
    async with httpx.AsyncClient(timeout=30) as client:
      resp = await client.post(url, json=payload)
      resp.raise_for_status()
    logger.debug("signal send → %s: %r", recipient, text[:80])

  async def run(self) -> None:
    """Listen for incoming messages via WebSocket, reconnecting on drop."""
    ws_endpoint = f"{self._ws_url}/v1/receive/{self._our_number}"
    logger.info("Signal listener connecting to %s", ws_endpoint)
    await asyncio.sleep(10)  # give signal-cli Docker container time to start

    while True:
      try:
        await self._listen(ws_endpoint)
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("Signal WebSocket error, reconnecting in 10s")
        await asyncio.sleep(10)

  async def _listen(self, url: str) -> None:
    # httpx doesn't support WebSocket; use the websockets library
    import websockets

    async with websockets.connect(url) as ws:  # type: ignore[attr-defined]
      logger.info("Signal listener connected")
      async for raw in ws:
        try:
          data = json.loads(raw)
        except json.JSONDecodeError:
          logger.warning("signal: bad JSON: %r", raw[:200])
          continue
        await self._handle(data)

  async def _handle(self, data: dict[str, Any]) -> None:
    try:
      envelope = data.get("envelope", {})
      sender = envelope.get("sourceNumber")
      if not sender:
        return

      dm = envelope.get("dataMessage", {})
      text = dm.get("message", "")
      if not text:
        return

      group_info = dm.get("groupInfo")
      if group_info:
        chat_id = f"group:{group_info.get('groupId', sender)}"
      else:
        chat_id = sender

      logger.debug("signal message from %s: %r", sender, text[:80])
      await self._on_message("signal", sender, chat_id, text)

    except (KeyError, TypeError):
      logger.debug("signal: unhandled envelope shape: %r", str(data)[:200])
