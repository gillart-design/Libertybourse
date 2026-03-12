"""Gestion des snapshots de portefeuille.

CORRECTIONS MAJEURES pour la courbe de pricing :
1. snapshot_min_delta abaissé à 0.01 (toute variation est capturée)
2. Suppression du filtre cash_delta/invested_delta qui empêchait les snapshots
3. Un snapshot est TOUJOURS créé si des positions sont investies et que l'intervalle minimum est respecté
4. Auto-refresh par défaut à "1" (activé) au lieu de "0"
"""
from __future__ import annotations

import pandas as pd

from simulator.constants import (
    DEFAULT_SNAPSHOT_MIN_DELTA,
    DEFAULT_SNAPSHOT_MIN_SECONDS,
)
from simulator.helpers import utc_now_iso


def upsert_snapshot(
    conn,
    snapshot: dict[str, float],
    explicit_event: str | None = None,
    explicit_label: str | None = None,
    min_delta_eur: float = DEFAULT_SNAPSHOT_MIN_DELTA,
    min_seconds: int = DEFAULT_SNAPSHOT_MIN_SECONDS,
) -> None:
    """Insère un snapshot de l'état du portefeuille.

    CORRIGÉ : enregistre un snapshot à chaque rafraîchissement si l'intervalle
    minimum est respecté, sans exiger de variation minimale. C'est ce qui permet
    à la courbe de montrer les variations réelles du portefeuille.
    """
    last = conn.execute(
        """
        SELECT captured_at_utc, portfolio_value, cash, invested
        FROM snapshots
        ORDER BY captured_at_utc DESC, id DESC
        LIMIT 1
        """
    ).fetchone()

    event_type = explicit_event
    event_label = explicit_label
    if last is not None:
        value_delta = float(snapshot["portfolio_value"] - last["portfolio_value"])
        last_ts = pd.Timestamp(last["captured_at_utc"])
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        elapsed = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds()

        if explicit_event is None:
            # Respecter l'intervalle minimum entre snapshots
            if elapsed < max(min_seconds, 1):
                return
            # CORRIGÉ : on enregistre TOUJOURS un snapshot quand l'intervalle est
            # respecté, même si la variation est nulle. Cela permet à la courbe
            # de montrer que le portefeuille est stable (ligne plate) ou qu'il varie.
            # L'ancien code avait un filtre min_delta qui empêchait les snapshots
            # quand la variation était faible → courbe plate artificielle.
            event_type = "UP" if value_delta >= 0 else "DOWN"
            event_label = f"{value_delta:+.2f} €"
        elif explicit_label is None:
            event_label = explicit_event
    else:
        event_type = event_type or "INIT"
        event_label = event_label or "Initialisation"

    conn.execute(
        """
        INSERT INTO snapshots(captured_at_utc, portfolio_value, cash, invested, pnl, pnl_pct, event_type, event_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            float(snapshot["portfolio_value"]),
            float(snapshot["cash"]),
            float(snapshot["invested"]),
            float(snapshot["pnl"]),
            float(snapshot["pnl_pct"]),
            event_type,
            event_label,
        ),
    )
    conn.commit()


def load_snapshots(conn) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT id, captured_at_utc, portfolio_value, cash, invested, pnl, pnl_pct, event_type, event_label
        FROM snapshots
        ORDER BY captured_at_utc ASC, id ASC
        """,
        conn,
        parse_dates=["captured_at_utc"],
    )
    if df.empty:
        return pd.DataFrame(
            columns=["id", "captured_at_utc", "portfolio_value", "cash", "invested", "pnl", "pnl_pct", "event_type", "event_label"]
        )
    return df
