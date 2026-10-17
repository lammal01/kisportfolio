from dataclasses import replace
from datetime import date

from agents.trading_agent import AGGRESSIVE_ADDENDUM, SYSTEM_PROMPT, _system_prompt
from config.settings import Settings


def _settings(until):
    return replace(Settings(), aggressive_until=until)


def test_aggressive_prompt_ends_after_until_date():
    s = _settings("2026-10-16")
    assert _system_prompt(s, date(2026, 10, 16)) == SYSTEM_PROMPT + AGGRESSIVE_ADDENDUM
    assert _system_prompt(s, date(2026, 10, 17)) == SYSTEM_PROMPT


def test_aggressive_prompt_off_by_default_and_on_bad_date():
    assert _system_prompt(_settings(None), date(2026, 10, 13)) == SYSTEM_PROMPT
    assert _system_prompt(_settings("not-a-date"), date(2026, 10, 13)) == SYSTEM_PROMPT
