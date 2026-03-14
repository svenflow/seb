"""Session pool — one SDKSession per contact, created on demand."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import anthropic

from seb.sdk_session import QueuedMessage, SDKSession

logger = logging.getLogger(__name__)

# send_fn(platform, chat_id_or_recipient, text)
type SendFn = Callable[[str, str, str], Coroutine[Any, Any, None]]

SESSIONS_FILE = Path(__file__).parent.parent / "state" / "sessions.json"

SOUL = """
You are seb — a personal AI assistant running on a Linux server.
You are reachable via Telegram and Signal.
You have access to the server (via Bash tool) and can help with anything.

Rules:
- You MUST call send-telegram or send-signal to reply. Messages are NEVER sent automatically.
- Always reply in the same channel the message came from (same platform, same chat_id/recipient).
- Be concise. Think carefully before calling tools.
- Admin tier contacts have full access. Trusted contacts have standard access.
"""


def _build_system_prompt(tier: str, platform: str, sender_id: str, model: str) -> str:
  from seb.sdk_session import MODEL_NAMES
  model_name = MODEL_NAMES.get(model, model)
  return (
    f"{SOUL}\n\n"
    f"<contact tier='{tier}' platform='{platform}' id='{sender_id}' />\n"
    f"<model id='{model}' name='{model_name}' />\n"
    f"<model_switching>User can switch models by prefixing messages: --haiku (default, fast/cheap), "
    f"--sonnet (balanced), --opus (smartest). Switches are sticky for 30 minutes then revert to haiku. "
    f"If asked what model you are, answer accurately using the model tag above.</model_switching>"
  )


class SDKBackend:
  """Manages a pool of Claude sessions, one per (platform, sender)."""

  def __init__(self, send_fn: SendFn, model: str, anthropic_api_key: str) -> None:
    self._send_fn = send_fn
    self._model = model
    self._client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
    self._sessions: dict[str, SDKSession] = {}
    self._saved_ids: dict[str, str] = self._load_session_ids()

  def _load_session_ids(self) -> dict[str, str]:
    if SESSIONS_FILE.exists():
      try:
        return json.loads(SESSIONS_FILE.read_text())
      except Exception:
        logger.warning("Could not load sessions.json")
    return {}

  def save_session_ids(self) -> None:
    ids = {
      key: s._session_id
      for key, s in self._sessions.items()
      if s._session_id is not None
    }
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(ids, indent=2))
    logger.info("Saved %d session IDs", len(ids))

  async def inject_message(self, msg: QueuedMessage) -> None:
    key = f"{msg.platform}:{msg.sender_id}"
    if key not in self._sessions:
      self._sessions[key] = self._create_session(key, msg)

    await self._sessions[key].inject(msg)

  def _create_session(self, key: str, msg: QueuedMessage) -> SDKSession:
    system_prompt = _build_system_prompt(msg.tier, msg.platform, msg.sender_id, self._model)
    saved_id = self._saved_ids.get(key)
    session = SDKSession(
      session_key=key,
      send_fn=self._send_fn,
      client=self._client,
      model=self._model,
      system_prompt=system_prompt,
      session_id=saved_id,
    )
    session.start()
    logger.info("Created session for %s (resumed=%s)", key, saved_id is not None)
    return session

  async def stop_all(self) -> None:
    self.save_session_ids()
    for session in self._sessions.values():
      await session.stop()
    self._sessions.clear()
