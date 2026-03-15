"""Per-contact Claude session backed by the Claude Agent SDK.

Replaces the raw Anthropic API with ClaudeSDKClient, which spawns a Claude Code
subprocess. This means:
- No API key needed (uses OAuth via `claude login`)
- Native tool support (Bash, Read, Write, Grep, etc.) — no manual tool defs
- Session persistence and resume built in
- Claude sends messages by calling send scripts via Bash tool
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from claude_agent_sdk import (
  ClaudeSDKClient,
  ClaudeAgentOptions,
  AssistantMessage,
  ResultMessage,
  SystemMessage,
  TextBlock,
  ToolUseBlock,
  ToolResultBlock,
  UserMessage,
  PermissionResultAllow,
  PermissionResultDeny,
  HookMatcher,
)

# Patch SDK message parser to handle unknown message types gracefully.
try:
  import claude_agent_sdk._internal.message_parser as _mp
  import claude_agent_sdk._internal.client as _client

  _original_parse = _mp.parse_message

  def _tolerant_parse(data):
    try:
      return _original_parse(data)
    except Exception:
      return SystemMessage(subtype=data.get("type", "unknown"), data=data)

  _client.parse_message = _tolerant_parse
except (ImportError, AttributeError):
  pass

if TYPE_CHECKING:
  from claude_agent_sdk.types import (
    SyncHookJSONOutput,
    HookContext,
    PreToolUseHookInput,
  )
  HookInputType = Any

logger = logging.getLogger(__name__)

ADMIN_COMMANDS = {"HEALME", "RESTART"}


class SDKSession:
  """
  Manages one persistent Claude Agent SDK session for a single contact.

  Uses concurrent send/receive architecture: a background receiver task
  runs receive_messages() continuously while the sender dispatches
  query() calls from the queue. This enables mid-turn steering.
  """

  def __init__(
    self,
    session_key: str,
    tier: str,
    cwd: str,
    model: str = "haiku",
    cli_path: Path | None = None,
    session_id: str | None = None,
  ) -> None:
    self.session_key = session_key
    self.tier = tier
    self.cwd = cwd
    self._model = model
    self._cli_path = cli_path
    self._session_id: str | None = session_id

    self._client: Optional[ClaudeSDKClient] = None
    self._queue: asyncio.Queue[str] = asyncio.Queue()
    self._task: Optional[asyncio.Task] = None
    self._pending_queries = 0
    self.running = False

    # Metrics
    self.turn_count = 0
    self.created_at = datetime.now()
    self.last_activity = datetime.now()
    self._error_count = 0

  @property
  def session_id(self) -> str | None:
    return self._session_id

  async def start(self) -> None:
    """Connect ClaudeSDKClient and start the message processing loop."""
    options = self._build_options()
    self._client = ClaudeSDKClient(options=options)
    await self._client.connect()
    self.running = True
    self._task = asyncio.create_task(self._run_loop(), name=f"session:{self.session_key}")
    logger.info("Session %s started (resume=%s)", self.session_key, self._session_id)

  async def stop(self) -> None:
    """Stop the session and kill the subprocess."""
    self.running = False
    if self._task:
      self._task.cancel()
      try:
        await self._task
      except asyncio.CancelledError:
        pass
    await self._kill_subprocess()
    logger.info("Session %s stopped (turns=%d)", self.session_key, self.turn_count)

  async def inject(self, text: str) -> None:
    """Queue a message for delivery to the Claude session."""
    await self._queue.put(text)
    logger.debug("Session %s: queued message (%d chars)", self.session_key, len(text))

  def is_alive(self) -> bool:
    return self.running and self._task is not None and not self._task.done()

  async def _kill_subprocess(self) -> None:
    """Kill the Claude CLI subprocess to prevent zombies."""
    if not self._client:
      return
    try:
      transport = getattr(self._client, "_transport", None)
      if transport:
        process = getattr(transport, "_process", None)
        if process and process.returncode is None:
          process.terminate()
          try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
          except asyncio.TimeoutError:
            process.kill()
    except Exception as e:
      logger.warning("Session %s: subprocess kill error: %s", self.session_key, e)
    finally:
      self._client = None

  async def _run_loop(self) -> None:
    """Main loop: start background receiver, then send queries from queue."""
    receiver = asyncio.create_task(self._receive_loop())
    try:
      while self.running:
        if receiver.done():
          logger.warning("Session %s: receiver crashed, exiting", self.session_key)
          break

        try:
          msg = await asyncio.wait_for(self._queue.get(), timeout=30)
        except asyncio.TimeoutError:
          continue

        if msg == "__SHUTDOWN__":
          break

        self.last_activity = datetime.now()
        self._pending_queries += 1

        try:
          assert self._client is not None
          await self._client.query(msg)
        except asyncio.CancelledError:
          raise
        except Exception as e:
          self._pending_queries = max(0, self._pending_queries - 1)
          self._error_count += 1
          logger.error("Session %s: query error #%d: %s", self.session_key, self._error_count, e)
          if self._error_count >= 3:
            self.running = False
            break
          await asyncio.sleep(2 * self._error_count)

    except asyncio.CancelledError:
      raise
    finally:
      receiver.cancel()
      try:
        await receiver
      except asyncio.CancelledError:
        pass
      await self._kill_subprocess()

  async def _receive_loop(self) -> None:
    """Background receiver: handle all messages from the SDK."""
    try:
      assert self._client is not None
      async for message in self._client.receive_messages():
        self._handle_message(message)
        if isinstance(message, ResultMessage):
          # Reset to 0 (not decrement) because multiple queued queries are
          # merged into a single turn by the SDK, producing one ResultMessage.
          # This mirrors how dispatch handles merged turns.
          self._pending_queries = 0
          self._error_count = 0
    except asyncio.CancelledError:
      pass
    except Exception as e:
      self._error_count += 1
      logger.error("Session %s: receiver error: %s", self.session_key, e)
      error_str = str(e).lower()
      is_fatal = "buffer" in error_str or "1048576" in error_str
      if is_fatal or self._error_count >= 3:
        self.running = False
        try:
          self._queue.put_nowait("__SHUTDOWN__")
        except Exception:
          pass

  def _handle_message(self, message: Any) -> None:
    """Log and track messages from the SDK."""
    if isinstance(message, AssistantMessage):
      for block in message.content:
        if isinstance(block, TextBlock):
          logger.info("Session %s OUT: %s", self.session_key, block.text[:200])
        elif isinstance(block, ToolUseBlock):
          logger.info("Session %s TOOL: %s", self.session_key, block.name)

    elif isinstance(message, ResultMessage):
      self.turn_count += message.num_turns or 0
      if message.session_id:
        self._session_id = message.session_id
      self.last_activity = datetime.now()
      logger.info(
        "Session %s TURN #%d | duration=%sms | error=%s",
        self.session_key,
        self.turn_count,
        message.duration_ms,
        message.is_error,
      )

    elif isinstance(message, SystemMessage):
      if hasattr(message, "data") and isinstance(message.data, dict):
        sid = message.data.get("session_id")
        if sid and not self._session_id:
          self._session_id = sid

  def _build_options(self) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions based on contact tier.

    Uses bypassPermissions for admin (with IS_SANDBOX=1 env var to allow
    running as root on Linux servers). Trusted/other tiers use "default"
    with a can_use_tool callback for restrictions.
    """
    if self.tier == "admin":
      tools = [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        "WebSearch", "WebFetch", "Task", "NotebookEdit",
      ]
      perm_mode = "bypassPermissions"
      turn_limit = 200
    elif self.tier == "trusted":
      tools = ["Read", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"]
      perm_mode = "default"
      turn_limit = 50
    else:
      # Default: restricted
      tools = ["Read", "Bash", "Glob", "Grep"]
      perm_mode = "default"
      turn_limit = 30

    # IS_SANDBOX=1 allows --dangerously-skip-permissions to work as root.
    # Without it, the Claude CLI refuses bypassPermissions for root/sudo.
    import os
    os.environ.setdefault("IS_SANDBOX", "1")

    opts = ClaudeAgentOptions(
      cwd=self.cwd,
      allowed_tools=tools,
      permission_mode=perm_mode,
      setting_sources=["project"],  # Load CLAUDE.md from cwd
      model=self._model,
      fallback_model="sonnet",
      max_turns=turn_limit,
      max_buffer_size=10 * 1024 * 1024,  # 10MB
    )

    # Custom CLI path (if configured)
    if self._cli_path:
      opts.cli_path = self._cli_path

    # Permission callback for non-admin tiers
    if self.tier in ("trusted",):
      opts.can_use_tool = self._permission_check

    # Session resume or fresh session
    if self._session_id:
      opts.extra_args = {"resume": self._session_id}
    else:
      opts.extra_args = {"session-id": str(uuid.uuid4())}

    return opts

  async def _permission_check(
    self, tool_name: str, tool_input: dict[str, Any], context: Any
  ) -> PermissionResultAllow | PermissionResultDeny:
    """Enforce tier-based tool restrictions for trusted contacts."""
    # Block file writes
    if tool_name in ("Write", "Edit", "NotebookEdit"):
      return PermissionResultDeny(message=f"{tool_name} blocked for trusted tier")

    # Block sensitive file reads
    if tool_name == "Read":
      path = tool_input.get("file_path", "")
      if any(s in path for s in [".ssh", ".env", "credentials", "secrets", "token"]):
        return PermissionResultDeny(message="Sensitive file blocked for trusted tier")

    return PermissionResultAllow()
