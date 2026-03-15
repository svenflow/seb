"""Shared fixtures for seb tests.

Provides a FakeSDKSession that replaces the real ClaudeSDKClient-backed
SDKSession so tests never spawn Claude subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure env vars required by Config don't blow up during tests
os.environ.setdefault("SIGNAL_NUMBER", "+15555550000")
os.environ.setdefault("ADMIN_SIGNAL_NUMBER", "+15555550001")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "111111")
os.environ.setdefault("TRUSTED_SIGNAL_NUMBERS", "+15555550002,+15555550003")
os.environ.setdefault("TRUSTED_TELEGRAM_IDS", "222222,333333")

# Reset the config singleton before each test module so env overrides take effect
import seb.config as _cfg_mod

_cfg_mod._config = None


class FakeSDKSession:
    """Drop-in replacement for seb.sdk_session.SDKSession.

    Records calls instead of spawning real Claude processes.
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
        self._session_id: str | None = session_id or str(uuid.uuid4())

        self.running = False
        self._pending_queries = 0
        self.turn_count = 0
        self._error_count = 0

        # Record calls for assertions
        self.injected: list[str] = []
        self.started = False
        self.stopped = False

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(self) -> None:
        self.running = True
        self.started = True

    async def stop(self) -> None:
        self.running = False
        self.stopped = True

    async def inject(self, text: str) -> None:
        self.injected.append(text)

    def is_alive(self) -> bool:
        return self.running


@pytest.fixture()
def tmp_state(tmp_path: Path):
    """Patch SESSIONS_FILE and TRANSCRIPTS_DIR to use temp directories."""
    sessions_file = tmp_path / "state" / "sessions.json"
    transcripts_dir = tmp_path / "transcripts"
    scripts_dir = tmp_path / "scripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    import seb.sdk_backend as backend_mod

    orig_sf = backend_mod.SESSIONS_FILE
    orig_td = backend_mod.TRANSCRIPTS_DIR
    orig_sd = backend_mod.SCRIPTS_DIR

    backend_mod.SESSIONS_FILE = sessions_file
    backend_mod.TRANSCRIPTS_DIR = transcripts_dir
    backend_mod.SCRIPTS_DIR = scripts_dir
    yield tmp_path
    backend_mod.SESSIONS_FILE = orig_sf
    backend_mod.TRANSCRIPTS_DIR = orig_td
    backend_mod.SCRIPTS_DIR = orig_sd


@pytest.fixture()
def patch_sdk_session(tmp_state):
    """Monkey-patch SDKBackend to use FakeSDKSession instead of the real one."""
    import seb.sdk_backend as backend_mod

    orig_cls = backend_mod.SDKSession

    backend_mod.SDKSession = FakeSDKSession
    yield tmp_state
    backend_mod.SDKSession = orig_cls


@pytest.fixture()
def backend(patch_sdk_session):
    """Return an SDKBackend wired to FakeSDKSession and temp dirs."""
    from seb.sdk_backend import SDKBackend

    return SDKBackend(model="haiku")


@pytest.fixture()
def config_patch():
    """Provide a mock Config for manager tests."""
    import seb.config as cfg_mod

    mock_cfg = MagicMock()
    mock_cfg.tier_for_signal.return_value = "admin"
    mock_cfg.tier_for_telegram.return_value = "admin"

    orig = cfg_mod.get_config

    def _mock_get():
        return mock_cfg

    cfg_mod.get_config = _mock_get
    yield mock_cfg
    cfg_mod.get_config = orig
