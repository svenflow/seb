"""send-telegram tool — Claude calls this to reply via Telegram."""

from __future__ import annotations

TOOL_DEFINITION = {
  "name": "send-telegram",
  "description": (
    "Send a message to a Telegram chat. You MUST call this tool to send any reply "
    "to a Telegram user — messages are never sent automatically."
  ),
  "input_schema": {
    "type": "object",
    "properties": {
      "chat_id": {
        "type": "string",
        "description": "The Telegram chat ID to send to (provided in the message context).",
      },
      "text": {
        "type": "string",
        "description": "The message text to send. Plain text or Markdown.",
      },
    },
    "required": ["chat_id", "text"],
  },
}
