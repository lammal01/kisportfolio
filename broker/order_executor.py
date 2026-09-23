"""Order execution against the KIS Open API.

Only ever called with orders that have already been approved by
risk.risk_manager. Must distinguish order acceptance from actual fill,
and must never trust HTTP 200 alone as confirmation of execution.
"""

from __future__ import annotations

# TODO(Phase 7/8): buy/sell/cancel/amend, order & fill status queries.
