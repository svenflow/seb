"""Permission check tests for SDKSession._permission_check.

Covers: default tier blocks Bash (except send scripts), blocks Write/Edit,
blocks sensitive reads. Trusted tier blocks writes but allows Bash.
"""

from __future__ import annotations

import pytest

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from seb.sdk_session import SDKSession


def _make_session(tier: str = "default") -> SDKSession:
  return SDKSession(
    session_key=f"test:{tier}",
    tier=tier,
    cwd="/tmp",
    model="haiku",
  )


# ---------------------------------------------------------------------------
# Default tier
# ---------------------------------------------------------------------------


async def test_default_blocks_bash():
  """Default tier should block arbitrary Bash commands."""
  session = _make_session("default")
  result = await session._permission_check("Bash", {"command": "ls -la /etc"}, None)
  assert isinstance(result, PermissionResultDeny)


async def test_default_allows_send_signal():
  """Default tier should allow Bash if the command contains a send script."""
  session = _make_session("default")
  result = await session._permission_check("Bash", {"command": "send-signal +1234 hello"}, None)
  assert isinstance(result, PermissionResultAllow)


async def test_default_allows_send_telegram():
  """Default tier should allow Bash if the command contains send-telegram."""
  session = _make_session("default")
  result = await session._permission_check("Bash", {"command": "/scripts/send-telegram 123 hi"}, None)
  assert isinstance(result, PermissionResultAllow)


async def test_default_blocks_write():
  """Default tier should block Write tool."""
  session = _make_session("default")
  result = await session._permission_check("Write", {"file_path": "/tmp/test.txt"}, None)
  assert isinstance(result, PermissionResultDeny)


async def test_default_blocks_edit():
  """Default tier should block Edit tool."""
  session = _make_session("default")
  result = await session._permission_check("Edit", {"file_path": "/tmp/test.txt"}, None)
  assert isinstance(result, PermissionResultDeny)


async def test_default_blocks_sensitive_read():
  """Default tier should block reads of sensitive files."""
  session = _make_session("default")
  for path in ["/home/user/.ssh/id_rsa", "/app/.env", "/etc/shadow"]:
    result = await session._permission_check("Read", {"file_path": path}, None)
    assert isinstance(result, PermissionResultDeny), f"Should block {path}"


async def test_default_allows_normal_read():
  """Default tier should allow reads of non-sensitive files."""
  session = _make_session("default")
  result = await session._permission_check("Read", {"file_path": "/tmp/hello.txt"}, None)
  assert isinstance(result, PermissionResultAllow)


async def test_default_allows_grep():
  """Default tier should allow Grep tool."""
  session = _make_session("default")
  result = await session._permission_check("Grep", {"pattern": "hello"}, None)
  assert isinstance(result, PermissionResultAllow)


# ---------------------------------------------------------------------------
# Trusted tier
# ---------------------------------------------------------------------------


async def test_trusted_allows_bash():
  """Trusted tier should allow Bash commands."""
  session = _make_session("trusted")
  result = await session._permission_check("Bash", {"command": "ls -la"}, None)
  assert isinstance(result, PermissionResultAllow)


async def test_trusted_blocks_write():
  """Trusted tier should block Write tool."""
  session = _make_session("trusted")
  result = await session._permission_check("Write", {"file_path": "/tmp/x"}, None)
  assert isinstance(result, PermissionResultDeny)


async def test_trusted_blocks_sensitive_read():
  """Trusted tier should block sensitive file reads."""
  session = _make_session("trusted")
  result = await session._permission_check("Read", {"file_path": "/home/.ssh/key"}, None)
  assert isinstance(result, PermissionResultDeny)


async def test_trusted_allows_normal_read():
  """Trusted tier should allow normal reads."""
  session = _make_session("trusted")
  result = await session._permission_check("Read", {"file_path": "/tmp/safe.txt"}, None)
  assert isinstance(result, PermissionResultAllow)
