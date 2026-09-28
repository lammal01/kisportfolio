"""telegram.bot command tests. KIS and Telegram are mocked; state goes
through a throwaway SQLite TradeMemory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from broker.kis_client import KisApiError, KisClient
from config.settings import Settings
from memory.trade_memory import TradeMemory
from models.decision import Decision
from risk.risk_manager import RiskManager
from telegram.bot import RELEASE_CODE_TTL_SECONDS, CommandBot

CHAT_ID = "12345"
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, now: float = T0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def memory(tmp_path):
    m = TradeMemory(tmp_path / "trades.sqlite3")
    yield m
    m.close()


@pytest.fixture
def kis():
    client = MagicMock(spec=KisClient)
    client.is_real_trading = False
    client.get_balance.return_value = {
        "holdings": [
            {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "2", "pchs_avg_pric": "70000",
             "prpr": "71000", "evlu_pfls_amt": "2000", "evlu_pfls_rt": "1.43"},
        ],
        "summary": {"dnca_tot_amt": "9860000", "scts_evlu_amt": "142000", "tot_evlu_amt": "10002000",
                    "evlu_pfls_smtl_amt": "2000"},
    }
    client.get_current_price.return_value = {"stck_prpr": "71000", "prdy_ctrt": "-1.20"}
    return client


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def bot(memory, kis, clock):
    return CommandBot(
        MagicMock(),
        memory,
        settings=Settings(telegram_bot_token="t", telegram_chat_id=CHAT_ID),
        kis_client_factory=lambda: kis,
        clock=clock,
    )


def _update(text: str, chat_id: str = CHAT_ID, date: float = T0 + 1, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "date": date, "text": text}}


def say(bot, text: str, **kw) -> str | None:
    return bot.handle_update(_update(text, **kw))


# --- access control ----------------------------------------------------------


def test_other_chats_are_ignored(bot, memory):
    assert say(bot, "/stop", chat_id="99999") is None
    assert memory.load_emergency_stop() is None


def test_messages_from_before_startup_are_dropped(bot, memory):
    memory.save_pause("x")
    assert say(bot, "/resume", date=T0 - 30) is None
    assert memory.load_pause() == "x"


def test_missing_chat_id_refuses_to_start(memory):
    with pytest.raises(Exception):
        CommandBot(MagicMock(), memory, settings=Settings(telegram_bot_token="t", telegram_chat_id=None))


def test_unknown_command_and_plain_text(bot):
    assert "알 수 없는 명령" in say(bot, "/rm_rf")
    assert say(bot, "hello") is None


def test_bot_suffix_is_accepted(bot):
    assert "명령어" in say(bot, "/help@MyTradingBot")


# --- read-only ----------------------------------------------------------------


def test_balance_and_positions(bot):
    balance = say(bot, "/balance")
    assert "10,002,000원" in balance and "보유종목: 1개" in balance
    positions = say(bot, "/positions")
    assert "삼성전자(005930) 2주" in positions and "1.43%" in positions


def test_price_validates_symbol(bot, kis):
    assert "사용법" in say(bot, "/price samsung")
    assert "71,000원" in say(bot, "/price 005930")
    kis.get_current_price.assert_called_once_with("005930")


def test_kis_error_is_reported_not_raised(bot, kis):
    kis.get_balance.side_effect = KisApiError("timeout")
    assert "/balance 실패" in say(bot, "/balance")


def test_status_shows_stop_and_pause(bot, memory):
    assert "비상 정지: 없음" in say(bot, "/status")
    RiskManager(store=memory).engage_emergency_stop("order outcome unknown")
    memory.save_pause("telegram /pause: lunch")
    status = say(bot, "/status")
    assert "order outcome unknown" in status and "lunch" in status


def test_trades_and_decision(bot, memory):
    assert "기록 없음" in say(bot, "/trades 005930")
    assert "기록된 판단 없음" in say(bot, "/decision")
    memory.record_decision(
        "d1", Decision("HOLD", "005930", 0, 0.6, ("flat trend",)), {}
    )
    assert "관망" in say(bot, "/trades 005930")
    decision = say(bot, "/decision")
    assert "관망" in decision and "- flat trend" in decision


# --- pause / resume -----------------------------------------------------------


def test_pause_then_resume_after_checks(bot, memory, kis):
    say(bot, "/pause lunch")
    assert "lunch" in memory.load_pause()
    assert "재개됨" in say(bot, "/resume")
    assert memory.load_pause() is None
    kis.get_balance.assert_called()


def test_resume_refused_while_emergency_stop_engaged(bot, memory):
    say(bot, "/pause")
    RiskManager(store=memory).engage_emergency_stop("order outcome unknown")
    assert "재개 거부" in say(bot, "/resume")
    assert memory.load_pause() is not None


def test_resume_refused_when_kis_unreachable(bot, memory, kis):
    say(bot, "/pause")
    kis.get_balance.side_effect = KisApiError("down")
    assert "재개 거부" in say(bot, "/resume")
    assert memory.load_pause() is not None


# --- stop / release ---------------------------------------------------------------


def test_stop_is_persisted_and_seen_by_running_risk_manager(bot, memory):
    running = RiskManager(store=memory)  # e.g. a cycle already in progress elsewhere
    say(bot, "/stop suspicious fills")
    assert "suspicious fills" in memory.load_emergency_stop()

    from risk.risk_manager import AccountState

    verdict = running.evaluate(
        Decision("BUY", "005930", 1, 0.9, ("x",)),
        "d1",
        AccountState("005930", 10_000_000, 10_000_000, 70_000, 0, open_order_count=0),
    )
    assert not verdict.approved and "비상 정지" in verdict.reason


def _code(reply: str) -> str:
    return reply.rsplit(" ", 1)[1]


def test_release_requires_matching_code(bot, memory):
    say(bot, "/stop")
    reply = say(bot, "/release_stop")
    assert "/release_stop " in reply
    assert memory.load_emergency_stop() is not None  # nothing released yet

    assert "해제됨" in say(bot, f"/release_stop {_code(reply)}")
    assert memory.load_emergency_stop() is None


def test_release_wrong_code_invalidates_pending(bot, memory):
    say(bot, "/stop")
    code = _code(say(bot, "/release_stop"))
    assert "불일치" in say(bot, "/release_stop 000000" if code != "000000" else "/release_stop 111111")
    assert "만료" in say(bot, f"/release_stop {code}")  # one attempt per code
    assert memory.load_emergency_stop() is not None


def test_release_code_expires(bot, memory, clock):
    say(bot, "/stop")
    code = _code(say(bot, "/release_stop"))
    clock.now += RELEASE_CODE_TTL_SECONDS + 1
    assert "만료" in say(bot, f"/release_stop {code}", date=clock.now)
    assert memory.load_emergency_stop() is not None


def test_release_when_not_engaged(bot):
    assert "걸려 있지 않습니다" in say(bot, "/release_stop")


# --- polling -------------------------------------------------------------------


def test_poll_once_replies_and_acknowledges(bot, memory):
    notifier = bot._notifier
    notifier.call.return_value = [
        _update("/pause", update_id=10),
        _update("/stop", chat_id="99999", update_id=11),
    ]

    offset = bot.poll_once(None)

    assert offset == 12  # both acknowledged, even the ignored one
    notifier.send_message.assert_called_once()
    assert memory.load_pause() is not None
    assert memory.load_emergency_stop() is None
