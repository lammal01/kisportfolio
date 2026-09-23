"""Entry point.

Currently a placeholder -- wires nothing up yet. From Phase 7 onward this
will run the Paper Trading loop; live trading stays disabled until every
phase up to it has been validated (see README Phase roadmap).
"""

from __future__ import annotations

from config.settings import KisCredentialError, get_settings


def main() -> None:
    settings = get_settings()
    try:
        _, _, _, environment = settings.resolve_kis_credentials()
        label = "실전투자 (REAL MONEY)" if environment == "real" else "모의투자 (paper)"
        print(f"KIS AI Trading Agent -- environment={environment} ({label})")
    except KisCredentialError as exc:
        print(f"KIS AI Trading Agent -- no KIS environment configured yet: {exc}")


if __name__ == "__main__":
    main()
