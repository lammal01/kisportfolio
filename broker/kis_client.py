"""Korea Investment & Securities (KIS) Open API client.

OAuth token issuance/caching, 현재가 조회 (Phase 2), 잔고/보유종목/
매수가능금액 조회 (Phase 3), 현금 매수/매도 주문 (Phase 7), and 일별
주문체결조회 (Phase 8). Endpoint/tr_id values are taken from the
official open-trading-api examples (examples_user/kis_auth.py and
examples_user/domestic_stock/domestic_stock_functions.py):
https://github.com/koreainvestment/open-trading-api

KIS issues a KakaoTalk notification on every *new* token issuance and
tokens are valid for ~24h, so a token is cached to disk and reused
until shortly before it expires -- callers should not need to think
about this.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from config.settings import KisCredentialError, KisEnvironment, Settings, get_settings

_PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"

_TOKEN_CACHE_PATH = Path(__file__).resolve().parent.parent / ".kis_token_cache.json"

# KIS access tokens are valid ~24h; refresh a bit early to be safe.
_EXPIRY_SAFETY_MARGIN = timedelta(minutes=5)

_KST = ZoneInfo("Asia/Seoul")

# Minimum spacing between REST calls. The 모의투자 server's per-second
# limit is very low ("초당 거래건수를 초과" seen with just 2-3 calls in a
# row); 실전 allows far more. Shared across KisClient instances since the
# limit is per app key, not per object.
_MIN_REQUEST_INTERVAL = {"paper": 0.5, "real": 0.05}
_throttle_lock = threading.Lock()
_last_request_at = 0.0

# Read-only GETs that hit the rate limit are retried this many times
# (orders never are -- see place_cash_order).
_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BACKOFF_SECONDS = 1.0

# inquire-daily-ccld returns 15 rows/page on 모의투자 (100 on 실전).
# Past this many pages we refuse rather than return a partial list.
_MAX_ORDER_PAGES = 20


class KisApiError(RuntimeError):
    """Raised for any non-successful KIS API response (rt_cd != '0')."""

    def __init__(self, message: str, msg_cd: str | None = None):
        super().__init__(message)
        self.msg_cd = msg_cd


class KisOrderUncertainError(KisApiError):
    """The order request may or may not have reached KIS (timeout, lost
    or unparseable response). Callers must treat the order as possibly
    placed -- never retry blindly, or it may be placed twice."""


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
        self._cano, self._acnt_prdt_cd = self._split_account_no(account_no)

    # Standard 주식 위탁계좌 product code -- used when only the 8-digit CANO
    # is given without an explicit 2-digit product code.
    _DEFAULT_ACNT_PRDT_CD = "01"

    @classmethod
    def _split_account_no(cls, account_no: str) -> tuple[str, str]:
        """KIS account numbers are 8 digits (CANO) + 2 digits (product code).

        Accepts "12345678-01", the bare "1234567801" form, or just the
        8-digit "12345678" (product code defaults to "01", the standard
        stock trading account).
        """
        digits = account_no.replace("-", "").strip()
        if len(digits) == 8 and digits.isdigit():
            return digits, cls._DEFAULT_ACNT_PRDT_CD
        if len(digits) == 10 and digits.isdigit():
            return digits[:8], digits[8:]
        raise KisApiError(
            "KIS account number must be 8 digits (CANO) or 8+2 digits "
            f"(CANO+product code), e.g. '12345678' or '12345678-01'; got {account_no!r}."
        )

    @property
    def _app_key_fingerprint(self) -> str:
        """Ties a cached token to the app key that issued it, so editing
        the app key in .env invalidates the cache (the raw key is never
        written to disk)."""
        return hashlib.sha256(self._app_key.encode()).hexdigest()[:16]

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
            if data.get("app_key_fingerprint") != self._app_key_fingerprint:
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
                    "app_key_fingerprint": self._app_key_fingerprint,
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

    def _throttle(self) -> None:
        global _last_request_at
        with _throttle_lock:
            wait = _last_request_at + _MIN_REQUEST_INTERVAL[self._environment] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_request_at = time.monotonic()

    @staticmethod
    def _is_rate_limited(body: dict) -> bool:
        return body.get("msg_cd") == "EGW00201" or "초당 거래건수" in str(body.get("msg1", ""))

    def _get_page(
        self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = ""
    ) -> tuple[dict, str]:
        """One GET against `path`. Returns (body, response tr_cont header)
        after checking rt_cd; read-only, so rate-limit rejections and
        network errors are retried. Raises KisApiError on any failure."""
        headers = self._headers(tr_id=tr_id)
        if tr_cont:
            headers["tr_cont"] = tr_cont
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            self._throttle()
            try:
                resp = requests.get(f"{self.base_url}{path}", headers=headers, params=params, timeout=10)
                body = resp.json() if resp.content else {}
            except (requests.RequestException, ValueError) as exc:
                # Reads are idempotent, so a lost/garbled response is retried.
                error = KisApiError(f"{path} failed: {exc!r}")
                retryable = True
            else:
                if resp.status_code == 200 and body.get("rt_cd") == "0":
                    return body, str(resp.headers.get("tr_cont", "")).strip()
                error = KisApiError(f"{path} failed: {body.get('msg1', body)}", msg_cd=body.get("msg_cd"))
                retryable = self._is_rate_limited(body)
            if not (retryable and attempt < _RATE_LIMIT_RETRIES):
                break
            time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
        raise error

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict:
        """GET request against `path`, returning the parsed body after
        checking rt_cd. Raises KisApiError on any failure."""
        return self._get_page(path, tr_id, params)[0]

    def get_current_price(self, symbol: str, market_div_code: str = "J") -> dict:
        """현재가 조회. symbol: 6-digit KRX code, e.g. '005930'."""
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": market_div_code,
                "FID_INPUT_ISCD": symbol,
            },
        )
        return body["output"]

    def get_balance(self) -> dict:
        """주식잔고조회: holdings (output1) + account summary (output2).

        Only fetches the first page (up to 20 rows on a 모의투자 account,
        50 on 실전투자) -- pagination via CTX_AREA_FK100/NK100 is not
        implemented yet since it isn't needed for a single small account.
        """
        tr_id = "VTTC8434R" if self._environment == "paper" else "TTTC8434R"
        body = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        holdings = [h for h in body.get("output1", []) if h.get("hldg_qty", "0") != "0"]
        summary_rows = body.get("output2") or [{}]
        return {"holdings": holdings, "summary": summary_rows[0]}

    def get_buying_power(
        self, symbol: str, order_unit_price: str = "0", order_division: str = "01"
    ) -> dict:
        """매수가능 조회 (주문가능금액/수량).

        order_division defaults to "01" (시장가) per KIS's own guidance --
        지정가("00") ignores the symbol's margin rate when computing the
        max quantity, so 시장가 is what should be used to check how much
        can actually be bought.
        """
        tr_id = "VTTC8908R" if self._environment == "paper" else "TTTC8908R"
        body = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            tr_id=tr_id,
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_UNPR": order_unit_price,
                "ORD_DVSN": order_division,
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        return body["output"]

    # (environment, side) -> tr_id, from the official order_cash example.
    _ORDER_TR_IDS = {
        ("paper", "BUY"): "VTTC0012U",
        ("paper", "SELL"): "VTTC0011U",
        ("real", "BUY"): "TTTC0012U",
        ("real", "SELL"): "TTTC0011U",
    }

    def place_cash_order(
        self,
        side: str,
        symbol: str,
        quantity: int,
        order_division: str = "01",
        unit_price: str = "0",
    ) -> dict:
        """주식주문(현금). Returns KIS's 'output' dict (ODNO/KRX_FWDG_ORD_ORGNO/
        ORD_TMD) once KIS has accepted the order -- acceptance, not a fill.

        order_division defaults to "01" (시장가, unit_price "0").

        Never retried here. Raises KisApiError when the order definitely
        wasn't placed (KIS explicitly rejected it, or the connection was
        never established), and KisOrderUncertainError when the outcome
        can't be known (possibly placed).
        """
        tr_id = self._ORDER_TR_IDS.get((self._environment, side))
        if tr_id is None:
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(f"quantity must be a positive integer, got {quantity!r}")

        path = "/uapi/domestic-stock/v1/trading/order-cash"
        # Built before sending: a token failure here means nothing was sent.
        headers = self._headers(tr_id=tr_id)
        self._throttle()
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                headers=headers,
                json={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "PDNO": symbol,
                    "ORD_DVSN": order_division,
                    "ORD_QTY": str(quantity),
                    "ORD_UNPR": unit_price,
                    "EXCG_ID_DVSN_CD": "KRX",
                    "SLL_TYPE": "01" if side == "SELL" else "",
                    "CNDT_PRIC": "",
                },
                timeout=10,
            )
        except requests.ConnectTimeout as exc:
            # The TCP connection was never established, so no request bytes
            # were sent -- definitely not placed.
            raise KisApiError(f"{path}: could not connect to KIS, order not sent ({exc!r})") from exc
        except requests.RequestException as exc:
            raise KisOrderUncertainError(f"{path}: no response from KIS ({exc!r})") from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise KisOrderUncertainError(
                f"{path}: unparseable response (HTTP {resp.status_code})"
            ) from exc

        rt_cd = body.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            raise KisApiError(f"{path} rejected: {body.get('msg1', body)}", msg_cd=body.get("msg_cd"))

        output = body.get("output") or {}
        if resp.status_code != 200 or rt_cd != "0" or not output.get("ODNO"):
            raise KisOrderUncertainError(
                f"{path}: ambiguous response (HTTP {resp.status_code}): {body}"
            )
        return output

    def get_daily_orders(self, date: str | None = None) -> list[dict]:
        """주식일별주문체결조회: every order placed on `date` (YYYYMMDD,
        default today KST), filled or not, newest first. Each row has
        odno/sll_buy_dvsn_cd/pdno/ord_qty/tot_ccld_qty/rmn_qty/avg_prvs/
        cncl_yn/rjct_qty/ord_tmd -- see models.order.OrderFill.

        Follows pagination to the end; raises KisApiError rather than
        return a truncated list, since callers use this to decide whether
        an order exists at all.
        """
        date = date or datetime.now(_KST).strftime("%Y%m%d")
        tr_id = "VTTC0081R" if self._environment == "paper" else "TTTC0081R"
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "INQR_STRT_DT": date,
            "INQR_END_DT": date,
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        rows: list[dict] = []
        tr_cont = ""
        for _ in range(_MAX_ORDER_PAGES):
            body, resp_tr_cont = self._get_page(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", tr_id, params, tr_cont
            )
            rows.extend(body.get("output1") or [])
            if resp_tr_cont not in ("F", "M"):
                return rows
            params["CTX_AREA_FK100"] = body.get("ctx_area_fk100", "")
            params["CTX_AREA_NK100"] = body.get("ctx_area_nk100", "")
            tr_cont = "N"
        raise KisApiError(f"daily order inquiry exceeded {_MAX_ORDER_PAGES} pages -- refusing a partial list")
