from __future__ import annotations

from pathlib import Path

import pytest

from midplane.graph import GraphDeps, build_graph, tool_allowlist, validate_tool_allowlist
from midplane.models import FakeModelProvider
from midplane.routing import DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE, CostLatencyRoutingPolicy
from midplane.store import SqliteEpisodeStore
from midplane.trading import PaperModeRefusal, PaperOnlyTradingControlClient


def test_non_paper_actions_are_refused():
    client = PaperOnlyTradingControlClient()
    with pytest.raises(PaperModeRefusal):
        client.submit({"mode": "live", "symbol": "AAPL", "side": "buy", "qty": 1})
    with pytest.raises(PaperModeRefusal):
        client.submit({"symbol": "AAPL", "side": "buy", "qty": 1})
    with pytest.raises(PaperModeRefusal):
        client.submit({"mode": "paper-trading", "symbol": "AAPL"})


def test_paper_mode_is_acknowledged_and_does_not_call_a_broker():
    client = PaperOnlyTradingControlClient()
    result = client.submit({"mode": "PAPER", "symbol": "AAPL", "side": "buy", "qty": 1})
    assert result["allowed"] is True
    assert result["mode"] == "paper"
    assert result["executed"] is False
    assert result["broker_called"] is False


def test_graph_allowlist_rejects_trading_and_broker_tools(store: SqliteEpisodeStore):
    assert tool_allowlist() == ()
    with pytest.raises(ValueError):
        validate_tool_allowlist(["broker_client"])
    with pytest.raises(ValueError):
        validate_tool_allowlist(["trading_control"])
    deps = GraphDeps(
        provider=FakeModelProvider(),
        store=store,
        policy=CostLatencyRoutingPolicy(),
        route_table=DEFAULT_ROUTE_TABLE,
        budget=DEFAULT_BUDGET,
        tools=("alpaca_order",),
    )
    with pytest.raises(ValueError):
        build_graph(deps)


def test_dependencies_do_not_declare_a_broker_package():
    text = Path("pyproject.toml").read_text().lower()
    for banned in ("ccxt", "alpaca", "ib_insync", "oanda", "binance"):
        assert banned not in text
