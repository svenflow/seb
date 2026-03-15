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
  Auto-accepts group invitations via POST /v1/groups/<number>/<groupid>/join
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

  async def accept_group_invite(self, group_id: str) -> bool:
    """Accept a pending group invitation via the REST API.

    POST /v1/groups/{number}/{groupid}/join
    Returns True if accepted, False on error.
    """
    url = f"{self._base_url}/v1/groups/{self._our_number}/{group_id}/join"
    try:
      async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url)
        if resp.status_code in (200, 204):
          logger.info("Accepted group invite: %s", group_id)
          return True
        else:
          logger.warning("Failed to accept group invite %s: %s %s", group_id, resp.status_code, resp.text[:200])
          return False
    except Exception as e:
      logger.error("Error accepting group invite %s: %s", group_id, e)
      return False

  async def run(self) -> None:
    """Listen for incoming messages via WebSocket, reconnecting on drop.

    On startup, automatically accepts any pending group invitations.
    """
    # Accept pending group invites before starting the listener
    await self._accept_pending_invites()

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

  async def _accept_pending_invites(self) -> None:
    """Fetch all groups and auto-accept any pending invitations."""
    url = f"{self._base_url}/v1/groups/{self._our_number}"
    try:
      async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        groups = resp.json()

      for group in groups:
        # Check if we're invited but haven't joined yet
        # The API returns groups with different member statuses
        if group.get("pending") or group.get("invited"):
          group_id = group.get("id") or group.get("internal_id")
          if group_id:
            logger.info("Auto-accepting pending group invite: %s (name=%s)", group_id, group.get("name", "unknown"))
            await self.accept_group_invite(group_id)

    except Exception as e:
      logger.warning("Could not check for pending group invites: %s", e)

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

      # Auto-accept group invitations when we receive a group update
      group_info = dm.get("groupInfo")
      if group_info:
        group_id = group_info.get("groupId", "")
        group_type = group_info.get("type")
        # If this looks like an invitation or we're not yet a member, try joining
        if group_type in ("DELIVER", "UPDATE") or not text:
          # Fire-and-forget: try to accept in case we're still pending
          asyncio.create_task(self._try_accept_if_pending(group_id))

        if text:
          chat_id = f"group:{group_id}"
          logger.debug("signal group message from %s in %s: %r", sender, group_id, text[:80])
          await self._on_message("signal", sender, chat_id, text)
        return

      if not text:
        return

      logger.debug("signal message from %s: %r", sender, text[:80])
      await self._on_message("signal", sender, sender, text)

    except (KeyError, TypeError):
      logger.debug("signal: unhandled envelope shape: %r", str(data)[:200])

  async def _try_accept_if_pending(self, group_id: str) -> None:
    """Try to accept a group invite. Silently ignores if already a member."""
    try:
      await self.accept_group_invite(group_id)
    except Exception:
      pass  # Already a member or other non-fatal error
