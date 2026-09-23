"""Entry point.

Currently a placeholder -- wires nothing up yet. From Phase 7 onward this
will run the Paper Trading loop; live trading stays disabled until every
phase up to it has been validated (see README Phase roadmap).
"""

from __future__ import annotations

from config.settings import get_settings


def main() -> None:
    settings = get_settings()
    print(f"KIS AI Trading Agent -- PAPER_TRADING={settings.paper_trading}")


if __name__ == "__main__":
    main()
