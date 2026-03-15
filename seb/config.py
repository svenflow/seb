import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
  """Return the value of an environment variable or raise a descriptive error."""
  value = os.environ.get(name)
  if value is None:
    raise EnvironmentError(
      f"Required environment variable {name} is not set. "
      f"Add it to your .env file or export it in your shell."
    )
  return value


@dataclass
class Config:
  # Claude Agent SDK — uses OAuth via `claude login`, no API key needed.
  # Model passed to ClaudeAgentOptions (e.g. "opus", "sonnet", "haiku").
  claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "haiku"))
  # Path to the `claude` CLI binary. If not set, uses the default system path.
  claude_cli_path: Path | None = field(
    default_factory=lambda: Path(os.environ["CLAUDE_CLI_PATH"]) if os.getenv("CLAUDE_CLI_PATH") else None
  )

  # Telegram (optional — omit TELEGRAM_BOT_TOKEN to disable)
  telegram_bot_token: str | None = field(
    default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN") or None
  )

  # Signal
  signal_number: str = field(default_factory=lambda: _require_env("SIGNAL_NUMBER"))
  # Base URL for signal-cli-rest-api (bbernhard Docker image)
  signal_api_url: str = field(
    default_factory=lambda: os.getenv("SIGNAL_API_URL", "http://127.0.0.1:6001")
  )

  # Admin identifiers
  admin_telegram_id: int | None = field(
    default_factory=lambda: int(os.environ["ADMIN_TELEGRAM_ID"]) if os.getenv("ADMIN_TELEGRAM_ID") else None
  )
  admin_signal_number: str = field(
    default_factory=lambda: _require_env("ADMIN_SIGNAL_NUMBER")
  )

  # Trusted contacts (in addition to admin)
  trusted_telegram_ids: set[int] = field(default_factory=set)
  trusted_signal_numbers: set[str] = field(default_factory=set)

  # Services
  memory_service_url: str = field(
    default_factory=lambda: os.getenv("MEMORY_SERVICE_URL", "http://localhost:7890")
  )

  def __post_init__(self) -> None:
    raw_tg = os.getenv("TRUSTED_TELEGRAM_IDS", "")
    if raw_tg:
      self.trusted_telegram_ids = {int(x.strip()) for x in raw_tg.split(",") if x.strip()}

    raw_sig = os.getenv("TRUSTED_SIGNAL_NUMBERS", "")
    if raw_sig:
      self.trusted_signal_numbers = {x.strip() for x in raw_sig.split(",") if x.strip()}

  def tier_for_telegram(self, user_id: int) -> str:
    if self.admin_telegram_id is not None and user_id == self.admin_telegram_id:
      return "admin"
    if user_id in self.trusted_telegram_ids:
      return "trusted"
    return "unknown"

  def tier_for_signal(self, number: str) -> str:
    if number == self.admin_signal_number:
      return "admin"
    if number in self.trusted_signal_numbers:
      return "trusted"
    return "unknown"


_config: Config | None = None


def get_config() -> Config:
  global _config
  if _config is None:
    _config = Config()
  return _config
