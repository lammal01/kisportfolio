"""Outbound Telegram notifications.

Phase 4 scope: a single send_message() primitive on top of the plain
Telegram Bot HTTP API (no python-telegram-bot dependency -- see README,
"KIS 공식 Toolkit" note on the telegram/python-telegram-bot name clash).
Decision/fill/rejection-specific message templates come in later phases
once models.decision / models.order actually exist.

Never includes API keys/secrets/tokens in message text. A Telegram outage
must not be able to take down the rest of the pipeline -- callers in
later phases should catch TelegramError around notification calls rather
than let it propagate into decision/order logic.
"""

from __future__ import annotations

import requests

from config.settings import Settings, get_settings


class TelegramError(RuntimeError):
    """Raised when the Telegram Bot API call itself fails."""


class TelegramNotifier:
    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            raise TelegramError(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID is missing -- cannot send notifications."
            )
        self._chat_id = settings.telegram_chat_id
        self._base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    def send_message(self, text: str, parse_mode: str = "HTML") -> dict:
        resp = requests.post(
            f"{self._base_url}/sendMessage",
            json={"chat_id": self._chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code != 200 or not body.get("ok"):
            raise TelegramError(f"sendMessage failed: {body.get('description', body)}")
        return body["result"]
