"""Config error message tests.

Covers: missing SIGNAL_NUMBER and ADMIN_SIGNAL_NUMBER raise EnvironmentError
with descriptive messages instead of bare KeyError.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_missing_signal_number_raises_environment_error():
  """Missing SIGNAL_NUMBER should raise EnvironmentError with a helpful message."""
  import seb.config as cfg_mod

  cfg_mod._config = None
  env = {k: v for k, v in os.environ.items() if k != "SIGNAL_NUMBER"}
  with patch.dict(os.environ, env, clear=True):
    with pytest.raises(EnvironmentError, match="SIGNAL_NUMBER"):
      cfg_mod.Config()


def test_missing_admin_signal_number_raises_environment_error():
  """Missing ADMIN_SIGNAL_NUMBER should raise EnvironmentError with a helpful message."""
  import seb.config as cfg_mod

  cfg_mod._config = None
  env = {k: v for k, v in os.environ.items() if k != "ADMIN_SIGNAL_NUMBER"}
  env["SIGNAL_NUMBER"] = "+15555550000"
  with patch.dict(os.environ, env, clear=True):
    with pytest.raises(EnvironmentError, match="ADMIN_SIGNAL_NUMBER"):
      cfg_mod.Config()


def test_config_loads_with_required_vars():
  """Config should load successfully when required env vars are set."""
  import seb.config as cfg_mod

  cfg_mod._config = None
  env = dict(os.environ)
  env["SIGNAL_NUMBER"] = "+15555550000"
  env["ADMIN_SIGNAL_NUMBER"] = "+15555550001"
  with patch.dict(os.environ, env, clear=True):
    cfg = cfg_mod.Config()
    assert cfg.signal_number == "+15555550000"
    assert cfg.admin_signal_number == "+15555550001"
