"""send-signal tool — Claude calls this to reply via Signal."""

from __future__ import annotations

TOOL_DEFINITION = {
  "name": "send-signal",
  "description": (
    "Send a message to a Signal number. You MUST call this tool to send any reply "
    "to a Signal user — messages are never sent automatically."
  ),
  "input_schema": {
    "type": "object",
    "properties": {
      "recipient": {
        "type": "string",
        "description": "The Signal phone number to send to (E.164 format, e.g. +12025551234).",
      },
      "text": {
        "type": "string",
        "description": "The message text to send.",
      },
    },
    "required": ["recipient", "text"],
  },
}
