"""Per-contact Claude session backed by the Anthropic Agent SDK."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anthropic

if TYPE_CHECKING:
  from seb.sdk_backend import SendFn

logger = logging.getLogger(__name__)

ADMIN_COMMANDS = {"HEALME", "RESTART"}

MODEL_ALIASES = {
  "--opus":   "claude-opus-4-6",
  "--sonnet": "claude-sonnet-4-6",
  "--haiku":  "claude-haiku-4-5-20251001",
}
MODEL_NAMES = {
  "claude-opus-4-6":           "Opus 4.6",
  "claude-sonnet-4-6":         "Sonnet 4.6",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
}


@dataclass
class QueuedMessage:
  platform: str  # "telegram" | "signal"
  sender_id: str
  chat_id: str
  text: str
  tier: str


class SDKSession:
  """
  Manages one persistent Claude conversation for a single contact.

  Messages are queued via asyncio.Queue so that mid-turn messages can be
  injected between tool calls without interrupting execution.
  """

  def __init__(
    self,
    session_key: str,
    send_fn: "SendFn",
    client: anthropic.AsyncAnthropic,
    model: str,
    system_prompt: str,
    session_id: str | None = None,
  ) -> None:
    self.session_key = session_key
    self._send_fn = send_fn
    self._client = client
    self._default_model = model
    self._model = model
    self._model_switched_at: float | None = None  # timestamp of last manual switch
    self._system_prompt = system_prompt
    self._session_id: str | None = session_id

    self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue()
    self._task: asyncio.Task[None] | None = None
    self._messages: list[dict[str, Any]] = []

  def start(self) -> None:
    self._task = asyncio.create_task(self._run_loop(), name=f"session:{self.session_key}")

  async def inject(self, msg: QueuedMessage) -> None:
    await self._queue.put(msg)

  async def stop(self) -> None:
    if self._task:
      self._task.cancel()
      try:
        await self._task
      except asyncio.CancelledError:
        pass

  async def _run_loop(self) -> None:
    logger.info("Session %s started", self.session_key)
    while True:
      try:
        msg = await self._queue.get()
        await self._process(msg)
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("Session %s: error processing message", self.session_key)

  async def _process(self, msg: QueuedMessage) -> None:
    text = msg.text

    # Revert to default model if sticky timeout (30 min) has elapsed
    MODEL_STICKY_SECS = 30 * 60
    if (
      self._model != self._default_model
      and self._model_switched_at is not None
      and time.monotonic() - self._model_switched_at > MODEL_STICKY_SECS
    ):
      logger.info("Session %s: model timeout, reverting %s → %s",
        self.session_key, self._model, self._default_model)
      self._model = self._default_model
      self._model_switched_at = None

    # Check for model switch prefix (case-insensitive), e.g. --opus, --sonnet, --haiku
    lower = text.strip().lower()
    for flag, model_id in MODEL_ALIASES.items():
      if lower.startswith(flag):
        old_model = self._model
        self._model = model_id
        self._model_switched_at = time.monotonic() if model_id != self._default_model else None
        text = text[len(flag):].lstrip()
        if old_model != model_id:
          logger.info("Session %s: switched model %s → %s", self.session_key, old_model, model_id)
          if not text:
            suffix = " (reverts to Haiku in 30 min)" if model_id != self._default_model else ""
            await self._send_fn(msg.platform, msg.chat_id, f"Switched to {MODEL_NAMES[model_id]}.{suffix}")
            return
        break

    wrapped = (
      f"<message platform='{msg.platform}' sender='{msg.sender_id}' "
      f"chat='{msg.chat_id}' tier='{msg.tier}'>\n{text}\n</message>"
    )
    self._messages.append({"role": "user", "content": wrapped})

    logger.debug("Session %s: calling Claude (%d msgs)", self.session_key, len(self._messages))

    # Agentic loop: call Claude, handle tool use, repeat until stop
    while True:
      response = await self._client.messages.create(
        model=self._model,
        max_tokens=8192,
        system=self._system_prompt,
        messages=self._messages,
        tools=self._tool_definitions(),
      )

      self._messages.append({"role": "assistant", "content": response.content})

      if response.stop_reason == "end_turn":
        break

      if response.stop_reason == "tool_use":
        tool_results = await self._handle_tool_calls(response.content, msg)
        self._messages.append({"role": "user", "content": tool_results})

        # Check for mid-turn injected messages
        pending = self._drain_queue()
        if pending:
          injection = "\n\n".join(
            f"<injected platform='{m.platform}' sender='{m.sender_id}'>\n{m.text}\n</injected>"
            for m in pending
          )
          self._messages.append({"role": "user", "content": injection})

        continue

      # Unknown stop reason — break to avoid infinite loop
      logger.warning("Session %s: unexpected stop_reason %r", self.session_key, response.stop_reason)
      break

  async def _handle_tool_calls(
    self, content: list[Any], msg: QueuedMessage
  ) -> list[dict[str, Any]]:
    results = []
    for block in content:
      if block.type != "tool_use":
        continue
      tool_name = block.name
      tool_input = block.input

      logger.info("Session %s: tool call %s %r", self.session_key, tool_name, tool_input)

      try:
        result = await self._execute_tool(tool_name, tool_input, msg)
      except Exception as exc:
        result = f"Error: {exc}"

      results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": str(result),
      })
    return results

  async def _execute_tool(
    self, name: str, input_: dict[str, Any], msg: QueuedMessage
  ) -> str:
    if name == "send-telegram":
      chat_id = input_["chat_id"]
      text = input_["text"]
      await self._send_fn("telegram", chat_id, text)
      return "sent"

    if name == "send-signal":
      recipient = input_["recipient"]
      text = input_["text"]
      await self._send_fn("signal", recipient, text)
      return "sent"

    # Bash tool — run arbitrary shell commands
    if name == "Bash" or name == "bash":
      import asyncio.subprocess as asp
      cmd = input_.get("command", "")
      proc = await asp.create_subprocess_shell(
        cmd,
        stdout=asp.PIPE,
        stderr=asp.STDOUT,
      )
      stdout, _ = await proc.communicate()
      return stdout.decode(errors="replace")[:8000]

    return f"Unknown tool: {name}"

  def _drain_queue(self) -> list[QueuedMessage]:
    items = []
    while not self._queue.empty():
      try:
        items.append(self._queue.get_nowait())
      except asyncio.QueueEmpty:
        break
    return items

  def _tool_definitions(self) -> list[dict[str, Any]]:
    from seb.tools.send_telegram import TOOL_DEFINITION as TG
    from seb.tools.send_signal import TOOL_DEFINITION as SIG
    return [
      TG,
      SIG,
      {
        "name": "Bash",
        "description": "Run a shell command on the server and return stdout+stderr.",
        "input_schema": {
          "type": "object",
          "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
          },
          "required": ["command"],
        },
      },
    ]
