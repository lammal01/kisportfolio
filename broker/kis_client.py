"""Korea Investment & Securities (KIS) Open API client.

Scope for Phase 2: OAuth token issuance/caching and 현재가 조회
(inquire-price). Endpoint/tr_id values are taken from the official
open-trading-api examples (examples_user/kis_auth.py and
examples_user/domestic_stock/domestic_stock_functions.py):
https://github.com/koreainvestment/open-trading-api

KIS issues a KakaoTalk notification on every *new* token issuance and
tokens are valid for ~24h, so a token is cached to disk and reused
until shortly before it expires -- callers should not need to think
about this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from config.settings import KisCredentialError, KisEnvironment, Settings, get_settings

_PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"

_TOKEN_CACHE_PATH = Path(__file__).resolve().parent.parent / ".kis_token_cache.json"

# KIS access tokens are valid ~24h; refresh a bit early to be safe.
_EXPIRY_SAFETY_MARGIN = timedelta(minutes=5)


class KisApiError(RuntimeError):
    """Raised for any non-successful KIS API response (rt_cd != '0')."""

    def __init__(self, message: str, msg_cd: str | None = None):
        super().__init__(message)
        self.msg_cd = msg_cd


@dataclass(frozen=True)
class _CachedToken:
    access_token: str
    expires_at: datetime


class KisClient:
    """Thin REST client.

    Which environment (paper/real) this talks to is resolved once, at
    construction time, from whichever KIS_PAPER_*/KIS_REAL_* credential set
    is fully filled in -- see config.settings.Settings.resolve_kis_credentials.
    Swapping environments is purely a .env change; no code path here reads a
    manual "which mode am I in" flag.
    """

    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        try:
            app_key, app_secret, account_no, environment = settings.resolve_kis_credentials()
        except KisCredentialError as exc:
            raise KisApiError(str(exc)) from exc

        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._environment: KisEnvironment = environment

    @property
    def environment(self) -> KisEnvironment:
        return self._environment

    @property
    def is_real_trading(self) -> bool:
        return self._environment == "real"

    @property
    def base_url(self) -> str:
        return _REAL_BASE_URL if self._environment == "real" else _PAPER_BASE_URL

    def _read_cached_token(self) -> _CachedToken | None:
        if not _TOKEN_CACHE_PATH.exists():
            return None
        try:
            data = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("base_url") != self.base_url:
                return None
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        if expires_at - _EXPIRY_SAFETY_MARGIN <= datetime.now():
            return None
        return _CachedToken(access_token=data["access_token"], expires_at=expires_at)

    def _write_cached_token(self, token: _CachedToken) -> None:
        _TOKEN_CACHE_PATH.write_text(
            json.dumps(
                {
                    "base_url": self.base_url,
                    "access_token": token.access_token,
                    "expires_at": token.expires_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        _TOKEN_CACHE_PATH.chmod(0o600)

    def get_access_token(self) -> str:
        cached = self._read_cached_token()
        if cached is not None:
            return cached.access_token

        resp = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code != 200 or "access_token" not in body:
            raise KisApiError(
                f"Token issuance failed (HTTP {resp.status_code}): {body}",
                msg_cd=body.get("error_code"),
            )

        expires_at = datetime.strptime(
            body["access_token_token_expired"], "%Y-%m-%d %H:%M:%S"
        )
        token = _CachedToken(access_token=body["access_token"], expires_at=expires_at)
        self._write_cached_token(token)
        return token.access_token

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_current_price(self, symbol: str, market_div_code: str = "J") -> dict:
        """현재가 조회. symbol: 6-digit KRX code, e.g. '005930'."""
        resp = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers(tr_id="FHKST01010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": market_div_code,
                "FID_INPUT_ISCD": symbol,
            },
            timeout=10,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code != 200 or body.get("rt_cd") != "0":
            raise KisApiError(
                f"inquire-price failed for {symbol}: {body.get('msg1', body)}",
                msg_cd=body.get("msg_cd"),
            )
        return body["output"]
