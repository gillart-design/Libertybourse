from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

# Force un dossier writable pour éviter les warnings de cache matplotlib.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-codex")
_mpl_config_dir = Path(os.environ["MPLCONFIGDIR"])
_mpl_config_dir.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(_mpl_config_dir)

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from .cycles import CyclePhase
from .metrics import rolling_vol_and_variance


def _ensure_dir(output_dir: str | Path) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out


def plot_efficient_frontier(frontier: pd.DataFrame, output_dir: str | Path) -> Path:
    out = _ensure_dir(output_dir)
    target = out / "efficient_frontier.png"
    required = {"volatility", "expected_return"}
    plt.figure(figsize=(9, 6))
    if frontier.empty or not required.issubset(frontier.columns):
        plt.text(0.5, 0.5, "Frontière efficiente indisponible", ha="center", va="center")
        plt.axis("off")
    else:
        sns.lineplot(data=frontier, x="volatility", y="expected_return", marker="o")
        plt.title("Frontière Efficiente (Markowitz)")
        plt.xlabel("Volatilité annualisée")
        plt.ylabel("Rendement annuel attendu")
    plt.tight_layout()
    plt.savefig(target, dpi=150)
    plt.close()
    return target


def plot_correlation_heatmap(returns: pd.DataFrame, output_dir: str | Path) -> Path:
    out = _ensure_dir(output_dir)
    target = out / "correlation_heatmap.png"
    plt.figure(figsize=(9, 7))
    sns.heatmap(returns.corr(), annot=True, cmap="RdBu_r", center=0)
    plt.title("Matrice de corrélation des actifs")
    plt.tight_layout()
    plt.savefig(target, dpi=150)
    plt.close()
    return target


def plot_portfolio_vol_curve(portfolio_returns: pd.Series, output_dir: str | Path, window: int = 30) -> Path:
    out = _ensure_dir(output_dir)
    target = out / "volatility_curve.png"
    rv = rolling_vol_and_variance(portfolio_returns, window=window)
    plt.figure(figsize=(10, 6))
    plt.plot(rv.index, rv["rolling_annualized_volatility"], label=f"Volatilité roulante {window}j")
    plt.plot(rv.index, rv["rolling_variance"], label=f"Variance roulante {window}j", alpha=0.7)
    plt.title("Courbe de volatilité et variance du portefeuille")
    plt.legend()
    plt.tight_layout()
    plt.savefig(target, dpi=150)
    plt.close()
    return target


def _slugify(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in name.strip())
    return "_".join(part for part in cleaned.split("_") if part)


def plot_portfolio_dashboard(name: str, portfolio_returns: pd.Series, output_dir: str | Path, window: int = 30) -> Path:
    """
    Graphique complet par portefeuille:
    - Performance cumulée
    - Drawdown
    - Volatilité/variance roulantes
    """
    out = _ensure_dir(output_dir)
    target = out / f"portfolio_{_slugify(name)}.png"
    r = portfolio_returns.dropna()
    wealth = (1 + r).cumprod()
    drawdown = wealth / wealth.cummax() - 1
    rv = rolling_vol_and_variance(r, window=window)

    fig, axes = plt.subplots(3, 1, figsize=(11, 12), sharex=True)
    axes[0].plot(wealth.index, wealth.values, color="#1f77b4")
    axes[0].set_title(f"{name} - Performance cumulée")
    axes[0].set_ylabel("Indice base 1")
    axes[0].grid(alpha=0.25)

    axes[1].fill_between(drawdown.index, drawdown.values, 0, color="#d62728", alpha=0.35)
    axes[1].set_title(f"{name} - Drawdown")
    axes[1].set_ylabel("Drawdown")
    axes[1].grid(alpha=0.25)

    axes[2].plot(rv.index, rv["rolling_annualized_volatility"], label=f"Vol annualisée {window}j", color="#2ca02c")
    axes[2].plot(rv.index, rv["rolling_variance"], label=f"Variance {window}j", color="#9467bd", alpha=0.75)
    axes[2].set_title(f"{name} - Volatilité et variance")
    axes[2].set_ylabel("Valeur")
    axes[2].grid(alpha=0.25)
    axes[2].legend()

    fig.tight_layout()
    fig.savefig(target, dpi=150)
    plt.close(fig)
    return target


def plot_portfolios_comparison(portfolios_returns: Mapping[str, pd.Series], output_dir: str | Path) -> Path:
    out = _ensure_dir(output_dir)
    target = out / "portfolios_comparison.png"
    plt.figure(figsize=(11, 6))

    for name, series in portfolios_returns.items():
        r = series.dropna()
        if r.empty:
            continue
        wealth = (1 + r).cumprod()
        plt.plot(wealth.index, wealth.values, label=name)

    plt.title("Comparaison des portefeuilles (base 1)")
    plt.ylabel("Indice de performance")
    plt.xlabel("Date")
    plt.legend()
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(target, dpi=150)
    plt.close()
    return target


def plot_market_cycle_phases(index_prices: pd.Series, phases: list[CyclePhase], output_dir: str | Path) -> Path:
    out = _ensure_dir(output_dir)
    target = out / "market_cycles.png"
    s = index_prices.dropna()

    plt.figure(figsize=(12, 6))
    plt.plot(s.index, s.values, color="black", linewidth=1.5, label="Indice")
    for p in phases:
        color = "#a1d99b" if p.phase == "bull" else "#fcbba1"
        plt.axvspan(p.start, p.end, color=color, alpha=0.25)
    plt.title("Cycles boursiers (régimes bull/bear)")
    plt.ylabel("Niveau indice")
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(target, dpi=150)
    plt.close()
    return target


def plot_dominant_cycles(cycles: pd.DataFrame, output_dir: str | Path) -> Path:
    out = _ensure_dir(output_dir)
    target = out / "dominant_cycles.png"
    if cycles.empty:
        plt.figure(figsize=(8, 4))
        plt.text(0.5, 0.5, "Données insuffisantes pour FFT", ha="center", va="center")
        plt.axis("off")
        plt.tight_layout()
        plt.savefig(target, dpi=150)
        plt.close()
        return target

    df = cycles.copy().sort_values("power", ascending=True).tail(10)
    plt.figure(figsize=(10, 6))
    plt.barh(df["period_days"].round().astype(int).astype(str), df["power"], color="#1f77b4")
    plt.title("Cycles dominants (Transformée de Fourier)")
    plt.xlabel("Puissance")
    plt.ylabel("Période (jours)")
    plt.tight_layout()
    plt.savefig(target, dpi=150)
    plt.close()
    return target
