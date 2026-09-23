"""Centralized application settings loaded from environment variables (.env).

Fail-closed defaults: PAPER_TRADING is true unless explicitly overridden,
and any missing broker credential should be treated as "trading disabled"
by callers, not silently ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))

    kis_app_key: str | None = field(default_factory=lambda: os.getenv("KIS_APP_KEY"))
    kis_app_secret: str | None = field(default_factory=lambda: os.getenv("KIS_APP_SECRET"))
    kis_account_no: str | None = field(default_factory=lambda: os.getenv("KIS_ACCOUNT_NO"))

    telegram_bot_token: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))

    # Fail-closed: real order execution must never be enabled by default.
    paper_trading: bool = field(default_factory=lambda: _get_bool("PAPER_TRADING", True))

    def has_kis_credentials(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret and self.kis_account_no)


def get_settings() -> Settings:
    return Settings()
