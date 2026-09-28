"""telegram.notifier tests. All HTTP calls are mocked -- no real Telegram
bot token or network access are needed to run this suite.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from config.settings import Settings
from telegram.notifier import TelegramError, TelegramNotifier


def _settings(**overrides) -> Settings:
    defaults = dict(telegram_bot_token="test-bot-token", telegram_chat_id="12345")
    defaults.update(overrides)
    return Settings(**defaults)


def test_missing_credentials_raises():
    with pytest.raises(TelegramError):
        TelegramNotifier(settings=_settings(telegram_bot_token=None))


@patch("telegram.notifier.requests.post")
def test_send_message_success(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 42}}

    notifier = TelegramNotifier(settings=_settings())
    result = notifier.send_message("hello")

    assert result == {"message_id": 42}
    called_url = mock_post.call_args.args[0]
    assert called_url == "https://api.telegram.org/bottest-bot-token/sendMessage"
    called_json = mock_post.call_args.kwargs["json"]
    assert called_json["chat_id"] == "12345"
    assert called_json["text"] == "hello"


@patch("telegram.notifier.requests.post")
def test_send_message_raises_on_api_error(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = {"ok": False, "description": "chat not found"}

    notifier = TelegramNotifier(settings=_settings())

    with pytest.raises(TelegramError):
        notifier.send_message("hello")


@patch("telegram.notifier.requests.post")
def test_send_message_raises_on_http_error(mock_post):
    mock_post.return_value.status_code = 401
    mock_post.return_value.content = b'{"ok": false, "description": "Unauthorized"}'
    mock_post.return_value.json.return_value = {"ok": False, "description": "Unauthorized"}

    notifier = TelegramNotifier(settings=_settings())

    with pytest.raises(TelegramError):
        notifier.send_message("hello")


@patch("telegram.notifier.requests.post")
def test_network_error_becomes_telegram_error_without_token(mock_post):
    import requests

    mock_post.side_effect = requests.ConnectionError(
        "HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded with url: /bottest-bot-token/sendMessage"
    )
    notifier = TelegramNotifier(settings=_settings())

    with pytest.raises(TelegramError) as excinfo:
        notifier.send_message("hello")
    assert "test-bot-token" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None  # original (token-bearing) error not chained


@patch("telegram.notifier.requests.post")
def test_send_message_without_parse_mode_omits_field(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = {"ok": True, "result": {}}
    TelegramNotifier(settings=_settings()).send_message("a < b", parse_mode="")
    assert "parse_mode" not in mock_post.call_args.kwargs["json"]
