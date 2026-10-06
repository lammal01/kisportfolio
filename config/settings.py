"""Centralized application settings loaded from environment variables (.env).

Fail-closed by design: which KIS environment (모의투자/paper vs 실전투자/real)
is active is derived automatically from which credential set is filled in --
never from a manually-flipped flag that could be left in the wrong state.
Exactly one of the two credential sets must be complete; anything else
(neither set complete, or both at once) is refused rather than guessed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

KisEnvironment = Literal["paper", "real"]


class KisCredentialError(RuntimeError):
    """Raised when the paper/real KIS credential sets are missing or ambiguous."""


@dataclass(frozen=True)
class Settings:
    # Claude API (Anthropic) -- used by agents.trading_agent for structured
    # BUY/SELL/HOLD decisions. Needs its own Console API key (console.anthropic.com),
    # separate from any Claude Code subscription; never picked up from the
    # ambient shell/CLI session, to keep credential sourcing explicit like
    # every other secret in this project.
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL") or "claude-opus-5")

    # 모의투자(paper trading) app key/secret/account. Fill only these to run
    # against the KIS paper trading server.
    kis_paper_app_key: str | None = field(default_factory=lambda: os.getenv("KIS_PAPER_APP_KEY"))
    kis_paper_app_secret: str | None = field(default_factory=lambda: os.getenv("KIS_PAPER_APP_SECRET"))
    kis_paper_account_no: str | None = field(default_factory=lambda: os.getenv("KIS_PAPER_ACCOUNT_NO"))

    # 실전투자(real trading) app key/secret/account. Filling these in (with
    # the paper set removed/blank) switches the whole app to real trading --
    # no code change needed, so treat this section of .env with care.
    kis_real_app_key: str | None = field(default_factory=lambda: os.getenv("KIS_REAL_APP_KEY"))
    kis_real_app_secret: str | None = field(default_factory=lambda: os.getenv("KIS_REAL_APP_SECRET"))
    kis_real_account_no: str | None = field(default_factory=lambda: os.getenv("KIS_REAL_ACCOUNT_NO"))

    telegram_bot_token: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))

    # risk.risk_manager limits -- deliberately conservative defaults; tune
    # via .env, never by editing risk_manager.py itself.
    risk_max_position_pct: float = field(
        default_factory=lambda: float(os.getenv("RISK_MAX_POSITION_PCT") or "0.10")
    )
    risk_daily_loss_limit_pct: float = field(
        default_factory=lambda: float(os.getenv("RISK_DAILY_LOSS_LIMIT_PCT") or "0.03")
    )
    risk_max_total_exposure_pct: float = field(
        default_factory=lambda: float(os.getenv("RISK_MAX_TOTAL_EXPOSURE_PCT") or "0.90")
    )

    # Phase 11 paper-trading loop (main.py --loop).
    # 삼성전자, SK하이닉스, 현대차, NAVER, LG에너지솔루션 by default.
    watchlist: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            code.strip()
            for code in (os.getenv("WATCHLIST") or "005930,000660,005380,035420,373220").split(",")
            if code.strip()
        )
    )
    cycle_minutes: int = field(default_factory=lambda: int(os.getenv("CYCLE_MINUTES") or "10"))
    ai_daily_call_limit: int = field(default_factory=lambda: int(os.getenv("AI_DAILY_CALL_LIMIT") or "50"))

    # memory.trade_memory SQLite file -- per machine, gitignored.
    trade_db_path: str = field(
        default_factory=lambda: os.getenv("TRADE_DB_PATH")
        or str(Path(__file__).resolve().parent.parent / "trade_memory.sqlite3")
    )

    def has_paper_credentials(self) -> bool:
        return bool(self.kis_paper_app_key and self.kis_paper_app_secret and self.kis_paper_account_no)

    def has_real_credentials(self) -> bool:
        return bool(self.kis_real_app_key and self.kis_real_app_secret and self.kis_real_account_no)

    def resolve_kis_credentials(self) -> tuple[str, str, str, KisEnvironment]:
        """Returns (app_key, app_secret, account_no, environment).

        Fail-closed: raises KisCredentialError unless exactly one of the two
        credential sets (paper / real) is fully filled in.
        """
        has_paper = self.has_paper_credentials()
        has_real = self.has_real_credentials()

        if has_paper and has_real:
            raise KisCredentialError(
                "Both KIS_PAPER_* and KIS_REAL_* credentials are set -- refusing to guess "
                "which environment to trade in. Fill in exactly one set (leave the other blank)."
            )
        if has_paper:
            return self.kis_paper_app_key, self.kis_paper_app_secret, self.kis_paper_account_no, "paper"
        if has_real:
            return self.kis_real_app_key, self.kis_real_app_secret, self.kis_real_account_no, "real"
        raise KisCredentialError(
            "No complete KIS credential set found. Fill in KIS_PAPER_APP_KEY/APP_SECRET/ACCOUNT_NO "
            "(모의투자) or KIS_REAL_APP_KEY/APP_SECRET/ACCOUNT_NO (실전투자) in .env."
        )


def get_settings() -> Settings:
    return Settings()
