from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portfolio_simulator_app as app


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    app.init_db(conn)
    return conn


def test_compute_positions_fifo_vs_lifo() -> None:
    tx = pd.DataFrame(
        [
            {
                "id": 1,
                "executed_at_utc": pd.Timestamp("2026-01-01", tz="UTC"),
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10.0,
                "price": 100.0,
                "fees": 0.0,
                "currency": "USD",
                "fx_to_base": 1.0,
                "strategy_tag": "test",
                "exchange": "XNYS",
                "note": None,
            },
            {
                "id": 2,
                "executed_at_utc": pd.Timestamp("2026-01-02", tz="UTC"),
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10.0,
                "price": 120.0,
                "fees": 0.0,
                "currency": "USD",
                "fx_to_base": 1.0,
                "strategy_tag": "test",
                "exchange": "XNYS",
                "note": None,
            },
            {
                "id": 3,
                "executed_at_utc": pd.Timestamp("2026-01-03", tz="UTC"),
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 10.0,
                "price": 130.0,
                "fees": 0.0,
                "currency": "USD",
                "fx_to_base": 1.0,
                "strategy_tag": "test",
                "exchange": "XNYS",
                "note": None,
            },
        ]
    )
    fifo = app.compute_positions(tx, accounting_method="fifo")
    lifo = app.compute_positions(tx, accounting_method="lifo")
    assert float(fifo.iloc[0]["realized_pnl"]) == 300.0
    assert float(lifo.iloc[0]["realized_pnl"]) == 100.0


def test_upsert_snapshot_respects_interval_and_delta() -> None:
    conn = make_conn()
    snap = {"portfolio_value": 100000.0, "cash": 50000.0, "invested": 50000.0, "pnl": 0.0, "pnl_pct": 0.0}
    app.upsert_snapshot(conn, snap, min_delta_eur=1.0, min_seconds=1)
    app.upsert_snapshot(conn, snap, min_delta_eur=1.0, min_seconds=60)
    rows = conn.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"]
    assert rows == 1


def test_trade_risk_blocks_cash_shortage() -> None:
    holdings = pd.DataFrame(columns=["symbol", "zone", "secteur", "valeur_marche"])
    errors = app.check_trade_risk(
        side="BUY",
        symbol="AAPL",
        quantity=100.0,
        price=200.0,
        fees=0.0,
        cash=1000.0,
        holdings=holdings,
        base_currency="EUR",
        fx_to_base=1.0,
        max_line_pct=50.0,
        max_sector_pct=60.0,
        max_zone_pct=70.0,
    )
    assert any("Cash insuffisant" in e for e in errors)


def test_trade_risk_handles_empty_holdings_without_columns() -> None:
    holdings = pd.DataFrame()
    errors = app.check_trade_risk(
        side="BUY",
        symbol="AAPL",
        quantity=1.0,
        price=100.0,
        fees=0.0,
        cash=1000.0,
        holdings=holdings,
        base_currency="EUR",
        fx_to_base=1.0,
        max_line_pct=100.0,
        max_sector_pct=100.0,
        max_zone_pct=100.0,
    )
    assert isinstance(errors, list)


def test_resolve_quick_sell_quantity_modes() -> None:
    qty_total, msg_total = app.resolve_quick_sell_quantity(
        mode="position_totale",
        held_qty=10.0,
        held_value_base=1000.0,
        unit_price_base=100.0,
        qty_input=0.0,
        amount_input=0.0,
        pct_input=0.0,
    )
    assert qty_total == 10.0
    assert msg_total == ""

    qty_amount, msg_amount = app.resolve_quick_sell_quantity(
        mode="montant_base",
        held_qty=10.0,
        held_value_base=1000.0,
        unit_price_base=100.0,
        qty_input=0.0,
        amount_input=250.0,
        pct_input=0.0,
    )
    assert qty_amount == 2.5
    assert msg_amount == ""

    qty_cap, msg_cap = app.resolve_quick_sell_quantity(
        mode="montant_base",
        held_qty=10.0,
        held_value_base=1000.0,
        unit_price_base=100.0,
        qty_input=0.0,
        amount_input=1500.0,
        pct_input=0.0,
    )
    assert qty_cap == 10.0
    assert "ramenée" in msg_cap


def test_create_evolution_chart_single_point_has_trace() -> None:
    snaps = pd.DataFrame(
        [
            {
                "captured_at_utc": pd.Timestamp("2026-03-05T12:00:00Z"),
                "portfolio_value": 100000.0,
                "event_type": "INIT",
                "event_label": "Init",
            }
        ]
    )
    fig = app.create_evolution_chart(snaps, currency="EUR")
    assert len(fig.data) >= 1


def test_polygon_symbol_supported() -> None:
    assert app.polygon_symbol_supported("AAPL")
    assert not app.polygon_symbol_supported("MC.PA")


def test_jwt_decode_hs256_valid_signature() -> None:
    import base64
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    def b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")

    secret = "test-secret"
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"email": "user@example.com", "exp": int(datetime.now(tz=timezone.utc).timestamp()) + 60}
    h = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("utf-8")
    sig = b64url(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    token = f"{h}.{p}.{sig}"
    decoded = app.jwt_decode_hs256(token, secret)
    assert decoded["email"] == "user@example.com"


def test_jwt_decode_hs256_rejects_bad_alg() -> None:
    import base64
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    def b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")

    secret = "test-secret"
    header = {"alg": "HS512", "typ": "JWT"}
    payload = {"email": "user@example.com", "exp": int(datetime.now(tz=timezone.utc).timestamp()) + 60}
    h = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("utf-8")
    sig = b64url(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    token = f"{h}.{p}.{sig}"
    with pytest.raises(ValueError):
        app.jwt_decode_hs256(token, secret)


def test_jwt_decode_hs256_rejects_expired_token() -> None:
    import base64
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    def b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")

    secret = "test-secret"
    now = int(datetime.now(tz=timezone.utc).timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"email": "user@example.com", "exp": now - 120}
    h = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("utf-8")
    sig = b64url(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    token = f"{h}.{p}.{sig}"
    with pytest.raises(ValueError):
        app.jwt_decode_hs256(token, secret)


def test_jwt_decode_hs256_rejects_future_nbf() -> None:
    import base64
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    def b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")

    secret = "test-secret"
    now = int(datetime.now(tz=timezone.utc).timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"email": "user@example.com", "exp": now + 600, "nbf": now + 600}
    h = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("utf-8")
    sig = b64url(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    token = f"{h}.{p}.{sig}"
    with pytest.raises(ValueError):
        app.jwt_decode_hs256(token, secret)


def test_user_db_path_is_stable_and_namespaced() -> None:
    p1 = app.user_db_path("User@Example.Com")
    p2 = app.user_db_path("user@example.com")
    assert p1 == p2
    assert "data/users/" in p1.as_posix()
    assert p1.name.endswith(".db")


def test_compute_portfolio_state_uses_market_price_for_unrealized() -> None:
    tx = pd.DataFrame(
        [
            {
                "id": 1,
                "executed_at_utc": pd.Timestamp("2026-01-01", tz="UTC"),
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1.0,
                "price": 100.0,
                "fees": 0.0,
                "currency": "USD",
                "fx_to_base": 1.0,
                "strategy_tag": "test",
                "exchange": "XNYS",
                "note": None,
            }
        ]
    )
    positions = app.compute_positions(tx, accounting_method="fifo")
    quotes = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "last": 120.0,
                "previous": 100.0,
                "change_pct": 20.0,
                "quote_time_utc": "2026-01-02T10:00:00+00:00",
                "market_state": "REGULAR",
                "currency": "USD",
                "source": "test",
                "regular_price": 120.0,
                "pre_price": 0.0,
                "post_price": 0.0,
                "official_close": 100.0,
                "price_context": "regular",
            }
        ]
    )
    holdings, _ = app.compute_portfolio_state(
        initial_capital=1000.0,
        transactions=tx,
        positions=positions,
        quotes=quotes,
        profiles={},
        base_currency="USD",
        fx_rates={"USD": 1.0},
    )
    assert float(holdings.iloc[0]["cours"]) == 120.0
    assert float(holdings.iloc[0]["pnl_latent"]) == 20.0
    assert float(holdings.iloc[0]["pnl_total_live"]) == 20.0


def test_simulate_order_execution_limit_pending() -> None:
    out = app.simulate_order_execution(
        side="BUY",
        order_type="LIMIT",
        market_price=100.0,
        quantity=2.0,
        trigger_price=90.0,
        slippage_bps=5.0,
        spread_bps=2.0,
        symbol="AAPL",
    )
    assert out["execution_status"] == "PENDING"
    assert float(out["executed_quantity"]) == 0.0


def test_run_backtest_includes_benchmark_and_costs(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = pd.date_range("2026-01-01", periods=80, freq="B")
    prices = pd.DataFrame(
        {
            "AAA": pd.Series(range(100, 180), index=dates, dtype=float),
            "BBB": pd.Series(range(90, 170), index=dates, dtype=float),
            "SPY": pd.Series(range(110, 190), index=dates, dtype=float),
        },
        index=dates,
    )

    monkeypatch.setattr(app, "fetch_history_for_backtest", lambda symbols, start, end: prices[list(symbols)])
    monkeypatch.setattr(app, "filter_prices_to_market_sessions", lambda frame, exchange: frame)

    curve, metrics = app.run_backtest(
        symbols=["AAA", "BBB"],
        start="2026-01-01",
        end="2026-06-01",
        initial_capital=100000.0,
        strategy="buy_hold",
        exchange="XNYS",
        benchmark_symbol="SPY",
        fees_bps=8.0,
        slippage_bps=5.0,
    )
    assert not curve.empty
    assert {"equity", "drawdown", "benchmark_equity", "cost_value"}.issubset(set(curve.columns))
    assert float(metrics["cum_fees_slippage"]) >= 0.0
    assert metrics["benchmark"] == "SPY"
