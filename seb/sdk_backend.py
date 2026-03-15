"""Session pool — one SDKSession per contact/group, created on demand.

Uses the Claude Agent SDK (ClaudeSDKClient) instead of the raw Anthropic API.
Each session runs as a Claude Code subprocess with OAuth authentication —
no API key needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

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
- To reply via Signal DM, run: {scripts_dir}/send-signal "<recipient>" "<message>"
- To reply to a Signal group, run: {scripts_dir}/send-signal-group "<group_id>" "<message>"
- To reply via Telegram, run: {scripts_dir}/send-telegram "<chat_id>" "<message>"
- You MUST call the appropriate send script via Bash to reply. Messages are NEVER sent automatically.
- CRITICAL: You MUST respond to EVERY incoming message by calling the send script. Never give \
a text-only response without calling the send script — the user will not see it.
- Always reply in the same channel the message came from (same platform, same chat_id/recipient).
- Be concise. Think carefully before calling tools.
- Admin tier contacts have full access. Trusted contacts have standard access.
"""

GROUP_SOUL = """\
You are seb — a personal AI assistant running on a Linux server, participating in a Signal group chat.

Rules:
- To reply to this group, run: {scripts_dir}/send-signal-group "{group_id}" "<message>"
- You MUST call the send script via Bash to reply. Messages are NEVER sent automatically.
- CRITICAL: You MUST respond to EVERY message that is addressed to you (mentions "seb" or asks \
you a question), regardless of who the sender is. This applies equally to all senders — admin, \
trusted, human, or AI. Never skip a response because you think it's "directed at" someone else \
when it clearly mentions your name.
- Messages come from different senders — check the sender field to know who's talking.
- Be concise. Think carefully before calling tools.
- Admin tier contacts have full access. Trusted contacts have standard access.
"""


def _build_system_prompt(tier: str, platform: str, sender_id: str, model: str) -> str:
  return (
    f"{SOUL.format(scripts_dir=SCRIPTS_DIR)}\n\n"
    f"<contact tier='{tier}' platform='{platform}' id='{sender_id}' />\n"
    f"<model id='{model}' />\n"
  )


def _build_group_system_prompt(
  group_id: str, platform: str, model: str
) -> str:
  return (
    f"{GROUP_SOUL.format(scripts_dir=SCRIPTS_DIR, group_id=group_id)}\n\n"
    f"<group id='{group_id}' platform='{platform}' />\n"
    f"<model id='{model}' />\n"
  )


_TIER_RANKS = {"admin": 3, "trusted": 2, "default": 1, "unknown": 0}


def _tier_rank(tier: str) -> int:
  """Return a numeric rank for a tier so we can compare them."""
  return _TIER_RANKS.get(tier, 1)


class SDKBackend:
  """Manages a pool of Claude Agent SDK sessions, one per contact or group."""

  def __init__(self, model: str, cli_path: Path | None = None) -> None:
    self._model = model
    self._cli_path = cli_path
    self._sessions: dict[str, SDKSession] = {}
    self._saved_ids: dict[str, str] = self._load_session_ids()
    self._lock = asyncio.Lock()

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
    tmp_file = SESSIONS_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(ids, indent=2))
    tmp_file.rename(SESSIONS_FILE)
    logger.info("Saved %d session IDs", len(ids))

  def clear_saved_session_ids(self) -> None:
    """Delete saved session IDs so sessions are created fresh on next message."""
    if SESSIONS_FILE.exists():
      SESSIONS_FILE.unlink()
    self._saved_ids.clear()
    logger.info("Cleared saved session IDs")

  async def inject_message(
    self,
    platform: str,
    sender_id: str,
    chat_id: str,
    text: str,
    tier: str,
  ) -> None:
    """Route a DM to the right session, creating one if needed."""
    key = f"{platform}:{sender_id}"
    async with self._lock:
      session = self._sessions.get(key)
      if session is not None and not session.is_alive():
        logger.warning("Dead session detected for %s, recreating", key)
        await session.stop()
        del self._sessions[key]
        session = None
      if session is None:
        self._sessions[key] = await self._create_session(key, tier, platform, sender_id)
      target = self._sessions[key]

    # Wrap message with metadata so Claude knows the context
    wrapped = (
      f"<message platform='{platform}' sender='{sender_id}' "
      f"chat='{chat_id}' tier='{tier}'>\n{text}\n</message>"
    )
    await target.inject(wrapped)

  async def inject_group_message(
    self,
    platform: str,
    sender_id: str,
    chat_id: str,
    text: str,
    tier: str,
  ) -> None:
    """Route a group message to the group session, creating one if needed."""
    # Group sessions are keyed by the group chat_id, not the sender
    key = f"{platform}:{chat_id}"
    async with self._lock:
      session = self._sessions.get(key)
      if session is not None and not session.is_alive():
        logger.warning("Dead group session detected for %s, recreating", key)
        await session.stop()
        del self._sessions[key]
        session = None
      # Group sessions keep the highest tier seen: if a higher-tier user joins,
      # the session is recreated with elevated permissions. This means permissions
      # never downgrade within a group — a deliberate trade-off for simplicity,
      # since mixed-tier groups are common and the alternative (per-message
      # permission switching) would add significant complexity.
      if session is not None and _tier_rank(tier) > _tier_rank(session.tier):
        logger.info(
          "Upgrading group session %s tier from %s to %s",
          key, session.tier, tier,
        )
        await session.stop()
        del self._sessions[key]
        session = None
      if session is None:
        self._sessions[key] = await self._create_group_session(key, tier, platform, chat_id)
      target = self._sessions[key]

    # Wrap with sender info so Claude knows who's talking
    wrapped = (
      f"<message platform='{platform}' sender='{sender_id}' "
      f"chat='{chat_id}' tier='{tier}' type='group'>\n{text}\n</message>"
    )
    await target.inject(wrapped)

  async def _create_session(
    self, key: str, tier: str, platform: str, sender_id: str
  ) -> SDKSession:
    # Create transcript directory for this contact
    transcript_dir = TRANSCRIPTS_DIR / platform / sender_id.replace("+", "_")
    transcript_dir.mkdir(parents=True, exist_ok=True)

    # Only write CLAUDE.md for fresh sessions (no saved ID to resume)
    claude_md = transcript_dir / "CLAUDE.md"
    saved_id = self._saved_ids.get(key)
    if not claude_md.exists() or saved_id is None:
      prompt = _build_system_prompt(tier, platform, sender_id, self._model)
      claude_md.write_text(prompt)

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

  async def _create_group_session(
    self, key: str, tier: str, platform: str, chat_id: str
  ) -> SDKSession:
    """Create a session for a group chat.

    Group sessions are shared by all members. The tier is set to admin
    if any admin is in the group, otherwise uses the triggering sender's tier.
    """
    # Sanitize group ID for filesystem (remove "group:" prefix, replace unsafe chars)
    safe_id = chat_id.replace("group:", "").replace("+", "_").replace("/", "_").replace("=", "")
    transcript_dir = TRANSCRIPTS_DIR / platform / f"group_{safe_id}"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    # Extract the raw group ID for the send script
    raw_group_id = chat_id.replace("group:", "")

    # Only write CLAUDE.md for fresh sessions (no saved ID to resume)
    claude_md = transcript_dir / "CLAUDE.md"
    saved_id = self._saved_ids.get(key)
    if not claude_md.exists() or saved_id is None:
      prompt = _build_group_system_prompt(raw_group_id, platform, self._model)
      claude_md.write_text(prompt)

    session = SDKSession(
      session_key=key,
      tier=tier,
      cwd=str(transcript_dir),
      model=self._model,
      cli_path=self._cli_path,
      session_id=saved_id,
    )
    await session.start()
    logger.info("Created group session for %s (resumed=%s)", key, saved_id is not None)
    return session

  async def stop_all(self) -> None:
    async with self._lock:
      self.save_session_ids()
      for session in self._sessions.values():
        await session.stop()
      self._sessions.clear()
