"""Telegram bot: control commands (/status /balance /positions /trades
/decision /pause /resume), restricted to a whitelist of chat IDs.

/resume must re-check system + risk manager state before actually
resuming trading, not just unconditionally honor the command.
"""

from __future__ import annotations

# TODO(Phase 10): command handlers + chat ID whitelist enforcement.
