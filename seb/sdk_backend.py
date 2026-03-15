"""Session pool — one SDKSession per contact, created on demand.

Uses the Claude Agent SDK (ClaudeSDKClient) instead of the raw Anthropic API.
Each session runs as a Claude Code subprocess with OAuth authentication —
no API key needed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from seb.sdk_session import SDKSession

logger = logging.getLogger(__name__)

SESSIONS_FILE = Path(__file__).parent.parent / "state" / "sessions.json"
TRANSCRIPTS_DIR = Path(__file__).parent.parent / "transcripts"
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

SOUL = """\
You are seb — a personal AI assistant running on a Linux server.
You are reachable via Telegram and Signal.
You have access to the server (via Bash tool) and can help with anything.

Rules:
- To reply via Signal, run: {scripts_dir}/send-signal "<recipient>" "<message>"
- To reply via Telegram, run: {scripts_dir}/send-telegram "<chat_id>" "<message>"
- You MUST call the appropriate send script via Bash to reply. Messages are NEVER sent automatically.
- Always reply in the same channel the message came from (same platform, same chat_id/recipient).
- Be concise. Think carefully before calling tools.
- Admin tier contacts have full access. Trusted contacts have standard access.
"""


def _build_system_prompt(tier: str, platform: str, sender_id: str, model: str) -> str:
  return (
    f"{SOUL.format(scripts_dir=SCRIPTS_DIR)}\n\n"
    f"<contact tier='{tier}' platform='{platform}' id='{sender_id}' />\n"
    f"<model id='{model}' />\n"
  )


class SDKBackend:
  """Manages a pool of Claude Agent SDK sessions, one per (platform, sender)."""

  def __init__(self, model: str, cli_path: Path | None = None) -> None:
    self._model = model
    self._cli_path = cli_path
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
      key: s.session_id
      for key, s in self._sessions.items()
      if s.session_id is not None
    }
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(ids, indent=2))
    logger.info("Saved %d session IDs", len(ids))

  async def inject_message(
    self,
    platform: str,
    sender_id: str,
    chat_id: str,
    text: str,
    tier: str,
  ) -> None:
    """Route a message to the right session, creating one if needed."""
    key = f"{platform}:{sender_id}"
    if key not in self._sessions:
      self._sessions[key] = await self._create_session(key, tier, platform, sender_id)

    # Wrap message with metadata so Claude knows the context
    wrapped = (
      f"<message platform='{platform}' sender='{sender_id}' "
      f"chat='{chat_id}' tier='{tier}'>\n{text}\n</message>"
    )
    await self._sessions[key].inject(wrapped)

  async def _create_session(
    self, key: str, tier: str, platform: str, sender_id: str
  ) -> SDKSession:
    # Create transcript directory for this contact
    transcript_dir = TRANSCRIPTS_DIR / platform / sender_id.replace("+", "_")
    transcript_dir.mkdir(parents=True, exist_ok=True)

    # Write a CLAUDE.md with the system prompt for this contact
    claude_md = transcript_dir / "CLAUDE.md"
    prompt = _build_system_prompt(tier, platform, sender_id, self._model)
    claude_md.write_text(prompt)

    saved_id = self._saved_ids.get(key)
    session = SDKSession(
      session_key=key,
      tier=tier,
      cwd=str(transcript_dir),
      model=self._model,
      cli_path=self._cli_path,
      session_id=saved_id,
    )
    await session.start()
    logger.info("Created session for %s (resumed=%s)", key, saved_id is not None)
    return session

  async def stop_all(self) -> None:
    self.save_session_ids()
    for session in self._sessions.values():
      await session.stop()
    self._sessions.clear()
