"""broker.kis_client tests. All HTTP calls are mocked -- no real KIS
credentials or network access are needed to run this suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from broker.kis_client import KisApiError, KisClient, _CachedToken
from config.settings import Settings


def _settings(**overrides) -> Settings:
    defaults = dict(
        kis_app_key="test-app-key",
        kis_app_secret="test-app-secret",
        kis_account_no="12345678-01",
        paper_trading=True,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _token_response(expires_in_minutes: int = 60) -> dict:
    expires_at = datetime.now() + timedelta(minutes=expires_in_minutes)
    return {
        "access_token": "fake-token",
        "access_token_token_expired": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        "token_type": "Bearer",
        "expires_in": expires_in_minutes * 60,
    }


@pytest.fixture(autouse=True)
def _isolated_token_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("broker.kis_client._TOKEN_CACHE_PATH", tmp_path / "kis_token_cache.json")


def test_missing_credentials_fails_closed():
    with pytest.raises(KisApiError):
        KisClient(settings=_settings(kis_app_key=None, kis_app_secret=None))


def test_uses_paper_trading_base_url_by_default():
    client = KisClient(settings=_settings(paper_trading=True))
    assert client.base_url == "https://openapivts.koreainvestment.com:29443"


def test_uses_real_base_url_when_paper_trading_disabled():
    client = KisClient(settings=_settings(paper_trading=False))
    assert client.base_url == "https://openapi.koreainvestment.com:9443"


@patch("broker.kis_client.requests.post")
def test_get_access_token_caches_across_calls(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = _token_response()

    client = KisClient(settings=_settings())
    first = client.get_access_token()
    second = client.get_access_token()

    assert first == second == "fake-token"
    mock_post.assert_called_once()  # second call served from disk cache


@patch("broker.kis_client.requests.post")
def test_get_access_token_refetches_once_expired(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = _token_response(expires_in_minutes=1)

    client = KisClient(settings=_settings())
    client._write_cached_token(
        _CachedToken(access_token="stale-token", expires_at=datetime.now() - timedelta(minutes=1))
    )

    token = client.get_access_token()

    assert token == "fake-token"
    mock_post.assert_called_once()


@patch("broker.kis_client.requests.post")
def test_get_access_token_raises_on_http_error(mock_post):
    mock_post.return_value.status_code = 401
    mock_post.return_value.content = b'{"error_description": "invalid appkey"}'
    mock_post.return_value.json.return_value = {"error_description": "invalid appkey"}

    client = KisClient(settings=_settings())

    with pytest.raises(KisApiError):
        client.get_access_token()


@patch("broker.kis_client.requests.get")
@patch("broker.kis_client.requests.post")
def test_get_current_price_returns_output_dict(mock_post, mock_get):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = _token_response()

    mock_get.return_value.status_code = 200
    mock_get.return_value.content = b"{}"
    mock_get.return_value.json.return_value = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": {"stck_prpr": "82100"},
    }

    client = KisClient(settings=_settings())
    result = client.get_current_price("005930")

    assert result == {"stck_prpr": "82100"}
    called_headers = mock_get.call_args.kwargs["headers"]
    assert called_headers["tr_id"] == "FHKST01010100"
    assert called_headers["authorization"] == "Bearer fake-token"


@patch("broker.kis_client.requests.get")
@patch("broker.kis_client.requests.post")
def test_get_current_price_raises_on_api_error(mock_post, mock_get):
    mock_post.return_value.status_code = 200
    mock_post.return_value.content = b"{}"
    mock_post.return_value.json.return_value = _token_response()

    mock_get.return_value.status_code = 200
    mock_get.return_value.content = b"{}"
    mock_get.return_value.json.return_value = {
        "rt_cd": "1",
        "msg_cd": "EGW00201",
        "msg1": "유효하지 않은 종목코드입니다.",
    }

    client = KisClient(settings=_settings())

    with pytest.raises(KisApiError):
        client.get_current_price("000000")
