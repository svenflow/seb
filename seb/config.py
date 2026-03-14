import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
  # Anthropic
  anthropic_api_key: str = field(default_factory=lambda: os.environ["ANTHROPIC_API_KEY"])
  claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"))

  # Telegram (optional — omit TELEGRAM_BOT_TOKEN to disable)
  telegram_bot_token: str | None = field(
    default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN") or None
  )

  # Signal
  signal_number: str = field(default_factory=lambda: os.environ["SIGNAL_NUMBER"])
  # Base URL for signal-cli-rest-api (bbernhard Docker image)
  signal_api_url: str = field(
    default_factory=lambda: os.getenv("SIGNAL_API_URL", "http://127.0.0.1:6001")
  )

  # Admin identifiers
  admin_telegram_id: int | None = field(
    default_factory=lambda: int(os.environ["ADMIN_TELEGRAM_ID"]) if os.getenv("ADMIN_TELEGRAM_ID") else None
  )
  admin_signal_number: str = field(
    default_factory=lambda: os.environ["ADMIN_SIGNAL_NUMBER"]
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
