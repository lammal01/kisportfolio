"""Outbound Telegram notifications.

A thin wrapper over the plain Telegram Bot HTTP API (no
python-telegram-bot dependency -- see README note on the
telegram/python-telegram-bot name clash): call() for any Bot API method,
send_message() for the configured chat. telegram.bot builds the command
interface on top of call().

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
        self._token = settings.telegram_bot_token
        self._base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    def _redact(self, text: str) -> str:
        """requests errors embed the full URL, which contains the bot token."""
        return text.replace(self._token, "<redacted>")

    def call(self, method: str, payload: dict, timeout: float = 10) -> object:
        """Calls a Bot API method; returns its 'result'. Every failure,
        network errors included, surfaces as TelegramError with the token
        stripped from the message."""
        try:
            resp = requests.post(f"{self._base_url}/{method}", json=payload, timeout=timeout)
            body = resp.json() if resp.content else {}
        except (requests.RequestException, ValueError) as exc:
            raise TelegramError(self._redact(f"{method} failed: {exc!r}")) from None
        if resp.status_code != 200 or not body.get("ok"):
            raise TelegramError(self._redact(f"{method} failed: {body.get('description', body)}"))
        return body["result"]

    def send_message(self, text: str, parse_mode: str = "HTML") -> dict:
        payload = {"chat_id": self._chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self.call("sendMessage", payload)
