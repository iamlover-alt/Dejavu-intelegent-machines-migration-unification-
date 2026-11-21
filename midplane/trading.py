"""External trading control service client.

This slice ships the interface only. The client refuses every action whose
mode is not paper. It has no broker SDK, no broker credentials, and no
network call. The graph does not register it as a tool.

RECONCILE with trading_control_service_spec.md and trading_risk_mitigation.md.
"""

from __future__ import annotations

from typing import Mapping, Protocol


class PaperModeRefusal(PermissionError):
    """The control service rejected an action because it is not paper mode."""


class TradingControlClient(Protocol):
    def submit(self, action: Mapping) -> Mapping:
        """Ask the external control service to accept one action."""


class PaperOnlyTradingControlClient:
    """Stub control-service client.

    Paper mode is acknowledged and not executed. Any other mode is refused.
    `broker_called` is always false: this object never places an order.
    """

    def submit(self, action: Mapping) -> dict:
        mode = str(action.get("mode", "")).strip().lower()
        if mode != "paper":
            raise PaperModeRefusal(
                "Trading control service refused the action: only paper mode is accepted."
            )
        return {
            "allowed": True,
            "mode": "paper",
            "executed": False,
            "broker_called": False,
            "reason": "paper-mode stub; this slice does not place orders",
        }
