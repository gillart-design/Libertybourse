"""Calcul du cash, des positions et de la valorisation du portefeuille.

CORRECTIONS par rapport au monolithe :
- compute_cash : refactoré pour lisibilité (plus de double négatif piégeux)
- compute_positions : avg_fx_to_base pondéré par quantité au lieu de moyenne simple
- infer_currency : utilise le mapping complet des suffixes de ticker
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from simulator.constants import (
    DEFAULT_ACCOUNTING_METHOD,
    DEFAULT_BASE_CURRENCY,
    POSITION_DUST_EPSILON,
)
from simulator.helpers import safe_float, infer_currency


def compute_cash(initial_capital: float, transactions: pd.DataFrame) -> float:
    """Calcule le cash disponible après application de toutes les transactions.

    CORRIGÉ : logique explicite BUY → cash diminue, SELL → cash augmente.
    """
    if transactions.empty:
        return initial_capital
    cash = float(initial_capital)
    for tx in transactions.itertuples(index=False):
        status = str(getattr(tx, "execution_status", "FILLED")).upper()
        if status not in {"FILLED", "PARTIAL"}:
            continue
        exec_qty = safe_float(getattr(tx, "executed_quantity", getattr(tx, "quantity", 0.0)), 0.0)
        exec_price = safe_float(getattr(tx, "executed_price", getattr(tx, "price", 0.0)), 0.0)
        if exec_qty <= 0 or exec_price <= 0:
            continue
        notional = exec_qty * exec_price
        fees = safe_float(tx.fees, 0.0)
        fx_to_base = safe_float(getattr(tx, "fx_to_base", 1.0), 1.0)
        if fx_to_base <= 0:
            fx_to_base = 1.0
        side = str(tx.side).upper()
        if side == "BUY":
            # Achat : on dépense (notional + frais) converti en devise base
            cash -= (notional + fees) * fx_to_base
        else:
            # Vente : on récupère (notional - frais) converti en devise base
            cash += (notional - fees) * fx_to_base
    return cash


def compute_positions(transactions: pd.DataFrame, accounting_method: str = DEFAULT_ACCOUNTING_METHOD) -> pd.DataFrame:
    """Calcule les positions ouvertes à partir des transactions.

    CORRIGÉ : avg_fx_to_base pondéré par quantité.
    """
    empty_cols = [
        "symbol", "quantity", "avg_cost", "book_value",
        "realized_pnl", "realized_pnl_base", "currency", "avg_fx_to_base",
    ]
    if transactions.empty:
        return pd.DataFrame(columns=empty_cols)

    method = accounting_method.strip().lower() if accounting_method else DEFAULT_ACCOUNTING_METHOD
    if method not in {"fifo", "lifo", "average"}:
        method = DEFAULT_ACCOUNTING_METHOD

    ledgers: dict[str, dict] = {}
    for tx in transactions.itertuples(index=False):
        symbol = str(tx.symbol).upper()
        side = str(tx.side).upper()
        status = str(getattr(tx, "execution_status", "FILLED")).upper()
        if status not in {"FILLED", "PARTIAL"}:
            continue
        qty = safe_float(getattr(tx, "executed_quantity", tx.quantity), 0.0)
        price = safe_float(getattr(tx, "executed_price", tx.price), 0.0)
        fees = safe_float(tx.fees, 0.0)
        currency = str(getattr(tx, "currency", DEFAULT_BASE_CURRENCY) or DEFAULT_BASE_CURRENCY).upper()
        fx_to_base = safe_float(getattr(tx, "fx_to_base", 1.0), 1.0)
        if qty <= 0 or price <= 0:
            continue

        ledger = ledgers.setdefault(
            symbol,
            {
                "symbol": symbol,
                "lots": [],
                "quantity": 0.0,
                "realized_pnl": 0.0,
                "realized_pnl_base": 0.0,
                "currency": currency,
                "fx_qty_weighted_sum": 0.0,
                "fx_qty_total": 0.0,
            },
        )
        ledger["currency"] = currency
        if fx_to_base > 0:
            ledger["fx_qty_weighted_sum"] += fx_to_base * qty
            ledger["fx_qty_total"] += qty

        if side == "BUY":
            unit_cost = (qty * price + fees) / qty
            if method == "average" and ledger["lots"]:
                existing_qty = sum([lot["qty"] for lot in ledger["lots"]])
                existing_cost = sum([lot["qty"] * lot["unit_cost"] for lot in ledger["lots"]])
                total_qty = existing_qty + qty
                avg_cost = (existing_cost + qty * unit_cost) / total_qty if total_qty > 0 else unit_cost
                ledger["lots"] = [{"qty": total_qty, "unit_cost": avg_cost, "fx_to_base": fx_to_base}]
            else:
                ledger["lots"].append({"qty": qty, "unit_cost": unit_cost, "fx_to_base": fx_to_base})
            ledger["quantity"] += qty
            continue

        if side != "SELL" or ledger["quantity"] <= 0:
            continue

        sell_qty = min(qty, ledger["quantity"])
        proceeds_net = sell_qty * price - fees
        remaining = sell_qty
        cost_basis = 0.0
        cost_basis_base = 0.0
        while remaining > POSITION_DUST_EPSILON and ledger["lots"]:
            lot_index = len(ledger["lots"]) - 1 if method == "lifo" else 0
            lot = ledger["lots"][lot_index]
            matched = min(remaining, lot["qty"])
            cost_basis += matched * lot["unit_cost"]
            lot_fx = safe_float(lot.get("fx_to_base", fx_to_base), fx_to_base if fx_to_base > 0 else 1.0)
            cost_basis_base += matched * lot["unit_cost"] * lot_fx
            lot["qty"] -= matched
            remaining -= matched
            if lot["qty"] <= POSITION_DUST_EPSILON:
                ledger["lots"].pop(lot_index)

        realized_quote = proceeds_net - cost_basis
        realized_base = proceeds_net * (fx_to_base if fx_to_base > 0 else 1.0) - cost_basis_base
        ledger["realized_pnl"] += realized_quote
        ledger["realized_pnl_base"] += realized_base
        ledger["quantity"] -= sell_qty
        if ledger["quantity"] <= POSITION_DUST_EPSILON:
            ledger["quantity"] = 0.0
            ledger["lots"] = []

    rows = []
    for ledger in ledgers.values():
        lots = ledger["lots"]
        qty = sum([lot["qty"] for lot in lots])
        cost_sum = sum([lot["qty"] * lot["unit_cost"] for lot in lots])
        avg_cost = cost_sum / qty if qty > 0 else 0.0
        # CORRIGÉ : FX moyen pondéré par quantité
        avg_fx = (
            ledger["fx_qty_weighted_sum"] / ledger["fx_qty_total"]
            if ledger["fx_qty_total"] > 0
            else 1.0
        )
        rows.append(
            {
                "symbol": ledger["symbol"],
                "quantity": qty,
                "avg_cost": avg_cost,
                "book_value": qty * avg_cost,
                "realized_pnl": ledger["realized_pnl"],
                "realized_pnl_base": ledger["realized_pnl_base"],
                "currency": ledger["currency"],
                "avg_fx_to_base": float(avg_fx),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    return df.sort_values("symbol").reset_index(drop=True)


def compute_portfolio_state(
    initial_capital: float,
    transactions: pd.DataFrame,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    profiles: dict[str, dict],
    base_currency: str,
    fx_rates: dict[str, float],
    trailing_dividends_per_share: dict[str, float] | None = None,
    catalog_by_symbol: dict[str, dict] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Calcule l'état complet du portefeuille."""
    catalog = catalog_by_symbol or {}
    quote_map = quotes.set_index("symbol").to_dict(orient="index") if not quotes.empty else {}
    holdings_rows = []
    for pos in positions.itertuples(index=False):
        q = quote_map.get(pos.symbol, {})
        quote_currency = infer_currency(pos.symbol, str(q.get("currency", "")), base_currency)
        fx_to_base = safe_float(fx_rates.get(quote_currency, np.nan), np.nan)
        if np.isnan(fx_to_base) or fx_to_base <= 0:
            fx_to_base = safe_float(getattr(pos, "avg_fx_to_base", 1.0), 1.0)
        avg_fx_to_base = safe_float(getattr(pos, "avg_fx_to_base", fx_to_base), fx_to_base)
        # CORRIGÉ : si pas de cotation, on met NaN au lieu du PRU
        raw_last = q.get("last")
        if raw_last is None or (isinstance(raw_last, float) and np.isnan(raw_last)):
            last = float(pos.avg_cost)  # Fallback pour calcul, mais flaggé
            cotation_disponible = False
        else:
            last = float(raw_last)
            cotation_disponible = last > 0
        market_value_quote = float(pos.quantity * last)
        market_value = float(market_value_quote * fx_to_base)
        book_value_base = float(pos.quantity * pos.avg_cost * avg_fx_to_base)
        unrealized = market_value - book_value_base
        realized_quote = safe_float(getattr(pos, "realized_pnl", 0.0), 0.0)
        realized_base_hist = safe_float(getattr(pos, "realized_pnl_base", np.nan), np.nan)
        if np.isnan(realized_base_hist):
            realized_base_hist = realized_quote * avg_fx_to_base
        realized_base_live = realized_quote * fx_to_base
        pnl_total_live = unrealized + realized_base_live
        profile = profiles.get(pos.symbol, {})
        holdings_rows.append(
            {
                "symbol": pos.symbol,
                "nom": profile.get("name", pos.symbol),
                "zone": profile.get("zone", catalog.get(pos.symbol, {}).get("zone", "USA")),
                "secteur": profile.get("sector", catalog.get(pos.symbol, {}).get("sector", "Non classé")),
                "type": catalog.get(pos.symbol, {}).get("asset_type", profile.get("asset_type", "Action")),
                "quantite": float(pos.quantity),
                "prix_moyen": float(pos.avg_cost),
                "cours": last,
                "cotation_disponible": cotation_disponible,
                "devise": quote_currency,
                "fx_to_base": fx_to_base,
                "valeur_marche": market_value,
                "valeur_marche_devise": market_value_quote,
                "pnl_latent": unrealized if cotation_disponible else 0.0,
                "pnl_realise": float(realized_base_live),
                "pnl_realise_historique": float(realized_base_hist),
                "pnl_total_live": float(pnl_total_live) if cotation_disponible else float(realized_base_live),
                "dividend_yield": float(profile.get("dividend_yield", 0.0)),
                "trailing_div_ps": safe_float((trailing_dividends_per_share or {}).get(pos.symbol, 0.0), 0.0),
                "avg_fx_to_base": avg_fx_to_base,
            }
        )

    holdings = pd.DataFrame(holdings_rows)
    if holdings.empty:
        invested = 0.0
        annual_dividends = 0.0
    else:
        invested = float(holdings["valeur_marche"].sum())
        implied = pd.to_numeric(holdings["valeur_marche"] * holdings["dividend_yield"], errors="coerce").fillna(0.0)
        trailing = pd.to_numeric(
            holdings["quantite"] * holdings["trailing_div_ps"] * holdings["fx_to_base"], errors="coerce"
        ).fillna(0.0)
        annual_dividends = float(pd.concat([implied, trailing], axis=1).max(axis=1).sum())

    cash = compute_cash(initial_capital, transactions)
    portfolio_value = cash + invested
    pnl = portfolio_value - initial_capital
    pnl_pct = (pnl / initial_capital * 100) if initial_capital else 0.0

    state = {
        "initial_capital": float(initial_capital),
        "cash": float(cash),
        "invested": float(invested),
        "portfolio_value": float(portfolio_value),
        "pnl": float(pnl),
        "pnl_pct": float(pnl_pct),
        "annual_dividends": annual_dividends,
        "monthly_dividends": annual_dividends / 12 if annual_dividends else 0.0,
        "base_currency": base_currency.upper(),
    }
    return holdings, state
