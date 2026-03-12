from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CyclePhase:
    start: pd.Timestamp
    end: pd.Timestamp
    phase: str
    duration_days: int


def detect_market_cycles(index_prices: pd.Series, short_window: int = 50, long_window: int = 200) -> list[CyclePhase]:
    """
    Détecte des phases haussières/baissières via croisement de moyennes mobiles.
    """
    s = index_prices.dropna().copy()
    if len(s) < long_window + 5:
        return []

    short_ma = s.rolling(short_window).mean()
    long_ma = s.rolling(long_window).mean()
    regime = (short_ma > long_ma).astype(int).dropna()

    phases: list[CyclePhase] = []
    if regime.empty:
        return phases

    last_regime = int(regime.iloc[0])
    phase_start = regime.index[0]

    for dt, r in regime.iloc[1:].items():
        if int(r) != last_regime:
            phase = "bull" if last_regime == 1 else "bear"
            phases.append(
                CyclePhase(
                    start=phase_start,
                    end=dt,
                    phase=phase,
                    duration_days=int((dt - phase_start).days),
                )
            )
            phase_start = dt
            last_regime = int(r)

    final_phase = "bull" if last_regime == 1 else "bear"
    phases.append(
        CyclePhase(
            start=phase_start,
            end=regime.index[-1],
            phase=final_phase,
            duration_days=int((regime.index[-1] - phase_start).days),
        )
    )
    return phases


def spectral_cycles(index_prices: pd.Series, max_period_days: int = 2000) -> pd.DataFrame:
    """
    Détection des cycles dominants via transformée de Fourier (proxy de périodicité).
    """
    s = index_prices.dropna().pct_change().dropna()
    if len(s) < 100:
        return pd.DataFrame(columns=["period_days", "power"])

    x = s.values - s.values.mean()
    fft = np.fft.rfft(x)
    power = np.abs(fft) ** 2
    freqs = np.fft.rfftfreq(len(x), d=1.0)

    mask = freqs > 0
    freqs = freqs[mask]
    power = power[mask]

    periods = 1 / freqs
    cycle_df = pd.DataFrame({"period_days": periods, "power": power})
    cycle_df = cycle_df[cycle_df["period_days"] <= max_period_days]
    cycle_df = cycle_df.sort_values("power", ascending=False).head(10)
    return cycle_df.reset_index(drop=True)


def kondratiev_proxy(index_prices: pd.Series) -> dict:
    """
    Proxy simplifié des cycles de Kondratiev (~40-70 ans).
    Nécessite idéalement des données mensuelles sur > 40 ans.
    """
    s = index_prices.dropna().resample("ME").last().dropna()
    span_years = len(s) / 12

    if span_years < 40:
        return {
            "available": False,
            "message": "Historique insuffisant pour approximer un cycle de Kondratiev (>= 40 ans requis).",
            "span_years": round(span_years, 2),
        }

    # Composante basse fréquence: moyenne mobile longue (10 ans)
    trend = s.rolling(window=120, min_periods=60).mean().dropna()
    rate = trend.pct_change(12).dropna()

    phase = "expansion_longue" if rate.iloc[-1] > 0 else "contraction_longue"
    return {
        "available": True,
        "span_years": round(span_years, 2),
        "current_phase_proxy": phase,
        "trend_yoy": float(rate.iloc[-1]) if not rate.empty else np.nan,
        "note": "Indicateur heuristique non prédictif; à croiser avec macroéconomie réelle.",
    }
