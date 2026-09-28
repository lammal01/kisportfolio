"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_kis_throttle(monkeypatch):
    # KisClient spaces out real calls to respect KIS rate limits; with
    # HTTP mocked there's nothing to protect, so don't slow tests down.
    monkeypatch.setattr("broker.kis_client._MIN_REQUEST_INTERVAL", {"paper": 0.0, "real": 0.0})
    monkeypatch.setattr("broker.kis_client._RATE_LIMIT_BACKOFF_SECONDS", 0.0)
