"""Telegram bot: control commands, restricted to the one configured chat.

    python -m telegram.bot

Long-polls getUpdates (plain Bot API over telegram.notifier, no
python-telegram-bot). Runs as its own process next to main.py; the two
share state only through memory.trade_memory (SQLite).

Safety rules:
- Only messages from TELEGRAM_CHAT_ID are handled; anything else is
  ignored without a reply.
- Messages sent before the bot started are dropped, so a stale /resume
  queued while it was offline never fires.
- /stop engages the persisted emergency stop; a running cycle's
  RiskManager re-reads it before every order.
- /release_stop needs a one-time code sent back within 60s -- releasing
  a stop should be a deliberate act, not a stray tap.
- /resume re-checks system state (emergency stop, KIS reachability)
  before lifting a pause, instead of unconditionally honoring it.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from broker.kis_client import KisApiError, KisClient
from config.settings import Settings, get_settings
from memory.trade_memory import TradeMemory
from risk.risk_manager import RiskManager
from telegram.labels import ACTION, FILL_STATUS, ORDER_STATUS, label
from telegram.notifier import TelegramError, TelegramNotifier

logger = logging.getLogger(__name__)

RELEASE_CODE_TTL_SECONDS = 60
_SYMBOL_RE = re.compile(r"^\d{6}$")

HELP = """명령어
/status - 환경, 비상 정지/일시정지 상태, 오늘 요약
/balance - 예수금/총평가/평가손익
/positions - 보유종목
/price 005930 - 현재가
/trades 005930 - 해당 종목 최근 판단/주문/체결
/decision - 가장 최근 AI 판단
/pause [사유] - 새 매매 사이클 중단
/resume - 시스템 점검 후 재개
/stop [사유] - 비상 정지 (모든 주문 즉시 차단)
/release_stop - 비상 정지 해제 (확인 코드 필요)"""


@dataclass
class _PendingRelease:
    code: str
    expires_at: float


def _won(value) -> str:
    try:
        return f"{float(value):,.0f}원"
    except (TypeError, ValueError):
        return "?"


class CommandBot:
    def __init__(
        self,
        notifier: TelegramNotifier,
        memory: TradeMemory,
        settings: Settings | None = None,
        kis_client_factory: Callable[[], KisClient] = KisClient,
        clock: Callable[[], float] = time.time,
    ):
        settings = settings or get_settings()
        if not settings.telegram_chat_id:
            raise TelegramError("TELEGRAM_CHAT_ID is missing -- refusing to accept commands from anyone.")
        self._allowed_chat_id = str(settings.telegram_chat_id)
        self._notifier = notifier
        self._memory = memory
        self._kis = kis_client_factory
        self._clock = clock
        self._started_at = clock()
        self._pending_release: _PendingRelease | None = None

    # --- dispatch -----------------------------------------------------------

    def handle_update(self, update: dict) -> str | None:
        """Returns the reply text, or None when the update must be ignored."""
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if chat_id != self._allowed_chat_id:
            if chat_id:
                logger.warning("ignored message from non-whitelisted chat %s", chat_id)
            return None
        if message.get("date", 0) < self._started_at:
            logger.info("dropped message sent before the bot started: %r", text[:40])
            return None
        if not text.startswith("/"):
            return None

        command, *args = text.split()
        command = command.split("@", 1)[0].lower()  # "/status@MyBot" in group chats
        handler = {
            "/start": self._help,
            "/help": self._help,
            "/status": self._status,
            "/balance": self._balance,
            "/positions": self._positions,
            "/price": self._price,
            "/trades": self._trades,
            "/decision": self._decision,
            "/pause": self._pause,
            "/resume": self._resume,
            "/stop": self._stop,
            "/release_stop": self._release_stop,
        }.get(command)
        logger.info("command %s args=%s", command, args if command != "/release_stop" else ["<code>"] * len(args))
        if handler is None:
            return f"알 수 없는 명령: {command}\n/help 로 목록 확인"
        try:
            return handler(args)
        except (KisApiError, TelegramError) as exc:
            return f"{command} 실패: {exc}"
        except Exception as exc:
            logger.exception("command %s failed", command)
            return f"{command} 처리 중 오류: {exc!r}"

    def _risk(self) -> RiskManager:
        return RiskManager(store=self._memory)

    # --- read-only ----------------------------------------------------------

    def _help(self, args: list[str]) -> str:
        return HELP

    def _status(self, args: list[str]) -> str:
        risk = self._risk()
        client = self._kis()
        env = "실전투자 (주문은 코드에서 차단됨)" if client.is_real_trading else "모의투자"
        lines = [f"환경: {env}"]
        lines.append(
            f"비상 정지: 걸림 -- {risk.emergency_stop_reason}" if risk.emergency_stop_engaged else "비상 정지: 없음"
        )
        pause = self._memory.load_pause()
        lines.append(f"일시정지: {pause}" if pause else "일시정지: 아님")
        start = self._memory.last_day_start_equity()
        if start:
            lines.append(f"시작 평가금액({start[0]}): {_won(start[1])}")
        lines.append(f"오늘 AI 판단: {self._memory.decision_count()}건")
        lines.append(f"오늘 실현손익(수수료 제외): {_won(self._memory.realized_pnl_total())}")
        return "\n".join(lines)

    def _balance(self, args: list[str]) -> str:
        balance = self._kis().get_balance()
        summary = balance["summary"]
        return "\n".join(
            [
                f"예수금: {_won(summary.get('dnca_tot_amt'))}",
                f"유가증권 평가: {_won(summary.get('scts_evlu_amt'))}",
                f"총평가금액: {_won(summary.get('tot_evlu_amt'))}",
                f"평가손익 합계: {_won(summary.get('evlu_pfls_smtl_amt'))}",
                f"보유종목: {len(balance['holdings'])}개",
            ]
        )

    def _positions(self, args: list[str]) -> str:
        holdings = self._kis().get_balance()["holdings"]
        if not holdings:
            return "보유종목 없음"
        lines = []
        for h in holdings:
            lines.append(
                f"{h.get('prdt_name', '?')}({h.get('pdno', '?')}) {h.get('hldg_qty', '?')}주\n"
                f"  평균 {_won(h.get('pchs_avg_pric'))} / 현재 {_won(h.get('prpr'))}\n"
                f"  평가손익 {_won(h.get('evlu_pfls_amt'))} ({h.get('evlu_pfls_rt', '?')}%)"
            )
        return "\n".join(lines)

    def _price(self, args: list[str]) -> str:
        if len(args) != 1 or not _SYMBOL_RE.match(args[0]):
            return "사용법: /price 005930"
        p = self._kis().get_current_price(args[0])
        return f"{args[0]} 현재가 {_won(p.get('stck_prpr'))} ({p.get('prdy_ctrt', '?')}%)"

    def _trades(self, args: list[str]) -> str:
        if len(args) != 1 or not _SYMBOL_RE.match(args[0]):
            return "사용법: /trades 005930"
        trades = self._memory.recent_trades(args[0])
        if not trades:
            return f"{args[0]} 기록 없음"
        lines = []
        for t in trades:
            line = (
                f"{t['at'][5:16]} {label(ACTION, t['action'])} {t['quantity']}주 -> "
                f"{label(ORDER_STATUS, t['order_status'])}"
            )
            if "fill" in t:
                line += f" / {label(FILL_STATUS, t['fill']['status'])} @ {_won(t['fill']['avg_price'])}"
            if "realized_pnl" in t:
                line += f" / 손익 {_won(t['realized_pnl'])}"
            lines.append(line)
        return "\n".join(lines)

    def _decision(self, args: list[str]) -> str:
        d = self._memory.latest_decision()
        if d is None:
            return "기록된 판단 없음"
        lines = [
            f"{d['created_at']} {d['symbol']} {label(ACTION, d['action'])} {d['quantity']}주 "
            f"(확신도 {d['confidence']:.0%})",
            f"주문: {label(ORDER_STATUS, d['order_status'])}"
            + (f" ({d['order_reason']})" if d["order_status"] == "REJECTED_BY_RISK" else ""),
        ]
        if d["fill_status"]:
            lines.append(f"체결: {label(FILL_STATUS, d['fill_status'])} {d['filled_qty']}주 @ {_won(d['avg_price'])}")
        lines.append("근거:")
        lines.extend(f"- {r}" for r in d["reason"])
        return "\n".join(lines)

    # --- control ------------------------------------------------------------

    def _pause(self, args: list[str]) -> str:
        reason = " ".join(args) or "사유 없음"
        self._memory.save_pause(f"telegram /pause: {reason}")
        return "일시정지됨 -- 새 매매 사이클이 시작되지 않습니다. /resume 으로 재개."

    def _resume(self, args: list[str]) -> str:
        if self._memory.load_pause() is None:
            return "일시정지 상태가 아닙니다."
        risk = self._risk()
        if risk.emergency_stop_engaged:
            return (
                f"재개 거부: 비상 정지가 걸려 있습니다 ({risk.emergency_stop_reason}).\n"
                "계좌 확인 후 /release_stop 먼저."
            )
        try:
            self._kis().get_balance()
        except KisApiError as exc:
            return f"재개 거부: KIS 조회 실패 ({exc})"
        self._memory.save_pause(None)
        return "점검 통과 (비상 정지 없음, KIS 조회 정상) -- 재개됨."

    def _stop(self, args: list[str]) -> str:
        reason = " ".join(args) or "사유 없음"
        self._risk().engage_emergency_stop(f"telegram /stop: {reason}")
        self._pending_release = None
        return "비상 정지 걸림 -- 모든 주문이 즉시 차단됩니다. 해제: /release_stop"

    def _release_stop(self, args: list[str]) -> str:
        risk = self._risk()
        if not risk.emergency_stop_engaged:
            self._pending_release = None
            return "비상 정지가 걸려 있지 않습니다."
        now = self._clock()
        if not args:
            code = f"{secrets.randbelow(10**6):06d}"
            self._pending_release = _PendingRelease(code, now + RELEASE_CODE_TTL_SECONDS)
            return (
                f"비상 정지 사유: {risk.emergency_stop_reason}\n"
                "계좌(보유종목/미체결)를 확인했다면 "
                f"{RELEASE_CODE_TTL_SECONDS}초 안에 보내세요:\n/release_stop {code}"
            )
        pending, self._pending_release = self._pending_release, None  # one attempt per code
        if pending is None or now > pending.expires_at:
            return "확인 코드가 없거나 만료됨 -- /release_stop 부터 다시."
        if args[0] != pending.code:
            return "확인 코드 불일치 -- /release_stop 부터 다시."
        reason = risk.emergency_stop_reason
        risk.disengage_emergency_stop()
        return f"비상 정지 해제됨 (이전 사유: {reason})"

    # --- polling loop -------------------------------------------------------

    def poll_once(self, offset: int | None, timeout: int = 25) -> int | None:
        """One getUpdates long-poll; replies to each handled command.
        Returns the next offset (acknowledging everything received)."""
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        updates = self._notifier.call("getUpdates", payload, timeout=timeout + 10)
        for update in updates:
            offset = update["update_id"] + 1
            reply = self.handle_update(update)
            if reply:
                try:
                    self._notifier.send_message(reply, parse_mode="")
                except TelegramError as exc:
                    logger.warning("reply failed: %s", exc)
        return offset

    def run_forever(self) -> None:
        offset = None
        logger.info("telegram bot started; accepting commands from chat %s only", self._allowed_chat_id)
        while True:
            try:
                offset = self.poll_once(offset)
            except TelegramError as exc:
                logger.warning("getUpdates failed: %s -- retrying in 5s", exc)
                time.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = CommandBot(TelegramNotifier(), TradeMemory())
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        logger.info("telegram bot stopped")


if __name__ == "__main__":
    main()
