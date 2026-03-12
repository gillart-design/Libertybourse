from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from portfolio_tool.cycles import detect_market_cycles, kondratiev_proxy, spectral_cycles
from portfolio_tool.data import (
    MarketClock,
    generate_synthetic_prices,
    get_market_clock,
    load_api_market_data,
    load_market_data,
    market_data_from_prices,
    parse_tickers,
)
from portfolio_tool.metrics import (
    annualized_return,
    annualized_volatility,
    beta_to_benchmark,
    max_drawdown,
    sortino_ratio,
    var_cvar,
)
from portfolio_tool.optimization import (
    PortfolioResult,
    efficient_frontier,
    equal_weight_portfolio,
    max_sharpe_portfolio,
    minimum_variance_portfolio,
    risk_parity_portfolio,
)
from portfolio_tool.report import (
    plot_correlation_heatmap,
    plot_dominant_cycles,
    plot_efficient_frontier,
    plot_market_cycle_phases,
    plot_portfolio_dashboard,
    plot_portfolios_comparison,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Outil clé en main de gestion de portefeuille boursier")
    parser.add_argument("--csv", type=str, default="", help="Chemin du CSV des prix")
    parser.add_argument("--tickers", type=str, default="", help="Liste de tickers séparés par des virgules (mode API)")
    parser.add_argument("--start", type=str, default="2015-01-01", help="Date de début pour l'API (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="", help="Date de fin pour l'API (YYYY-MM-DD, optionnel)")
    parser.add_argument("--interval", type=str, default="1d", help="Intervalle API (ex: 1d, 1h)")
    parser.add_argument("--exchange", type=str, default="XNYS", help="Code exchange pour calendrier marché (ex: XNYS, XPAR)")
    parser.add_argument(
        "--ignore-market-hours",
        action="store_true",
        help="Ne pas filtrer aux sessions officielles de marché",
    )
    parser.add_argument("--risk-free", type=float, default=0.02, help="Taux sans risque annuel")
    parser.add_argument("--allow-short", action="store_true", help="Autoriser les ventes à découvert")
    parser.add_argument("--frontier-points", type=int, default=40, help="Nombre de points sur la frontière efficiente")
    parser.add_argument("--output-dir", type=str, default="output", help="Dossier de sortie")
    parser.add_argument("--backup-dir", type=str, default="", help="Dossier des backups (défaut: output/backups)")
    parser.add_argument("--no-backup", action="store_true", help="Désactiver la sauvegarde automatique")
    parser.add_argument("--benchmark", type=str, default="", help="Nom de l'actif benchmark dans les colonnes")
    return parser.parse_args()


def _compute_metrics(
    portfolio_returns: pd.Series,
    result: PortfolioResult,
    risk_free_rate: float,
    benchmark_returns: pd.Series | None = None,
) -> dict:
    out = {
        "expected_return_annual": result.expected_return,
        "volatility_annual": result.volatility,
        "sharpe": result.sharpe,
        "sortino": sortino_ratio(portfolio_returns, risk_free_rate=risk_free_rate),
        "max_drawdown": max_drawdown(portfolio_returns),
        "var_95": var_cvar(portfolio_returns, alpha=0.95)[0],
        "cvar_95": var_cvar(portfolio_returns, alpha=0.95)[1],
        "annualized_return_realized": annualized_return(portfolio_returns),
        "annualized_vol_realized": annualized_volatility(portfolio_returns),
    }
    if benchmark_returns is not None:
        out["beta_vs_benchmark"] = beta_to_benchmark(portfolio_returns, benchmark_returns)
    return out


def _portfolio_rows(results: dict[str, PortfolioResult]) -> pd.DataFrame:
    rows = []
    for name, result in results.items():
        for asset, weight in result.weights.items():
            rows.append({"portfolio": name, "asset": asset, "weight": float(weight)})
    return pd.DataFrame(rows)


def _backup_output_files(output_dir: Path, backup_root: Path) -> Path:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_root / stamp
    target.mkdir(parents=True, exist_ok=True)

    backup_root_abs = backup_root.resolve()
    for path in output_dir.iterdir():
        if path.resolve() == backup_root_abs:
            continue
        if path.is_file():
            shutil.copy2(path, target / path.name)
    return target


def _safe_market_clock(exchange: str) -> tuple[MarketClock | None, str | None]:
    try:
        return get_market_clock(exchange=exchange), None
    except ImportError as exc:
        return None, str(exc)
    except Exception as exc:  # pragma: no cover - garde-fou runtime
        return None, f"Impossible de récupérer l'horloge marché: {exc}"


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    enforce_market_sessions = not args.ignore_market_hours
    end_date = args.end or None

    source = "synthetic"
    data_warning = None
    if args.csv:
        try:
            market = load_market_data(
                args.csv,
                exchange=args.exchange,
                enforce_market_sessions=enforce_market_sessions,
            )
        except ImportError as exc:
            if enforce_market_sessions and "pandas-market-calendars" in str(exc):
                data_warning = f"{exc} | filtrage horaires désactivé automatiquement."
                market = load_market_data(args.csv, exchange=args.exchange, enforce_market_sessions=False)
            else:
                raise
        source = f"csv:{Path(args.csv).name}"
    elif args.tickers:
        try:
            market = load_api_market_data(
                tickers=args.tickers,
                start=args.start,
                end=end_date,
                interval=args.interval,
                exchange=args.exchange,
                enforce_market_sessions=enforce_market_sessions,
            )
        except ImportError as exc:
            if enforce_market_sessions and "pandas-market-calendars" in str(exc):
                data_warning = f"{exc} | filtrage horaires désactivé automatiquement."
                market = load_api_market_data(
                    tickers=args.tickers,
                    start=args.start,
                    end=end_date,
                    interval=args.interval,
                    exchange=args.exchange,
                    enforce_market_sessions=False,
                )
            else:
                raise
        source = f"api:yahoo:{','.join(parse_tickers(args.tickers))}"
    else:
        prices = generate_synthetic_prices()
        market = market_data_from_prices(prices)

    prices = market.prices
    returns = market.returns
    if returns.empty:
        raise ValueError("Aucun rendement disponible après chargement des données.")

    benchmark_col = args.benchmark.strip()
    benchmark_returns = None
    investable_cols = list(returns.columns)
    if benchmark_col and benchmark_col in returns.columns:
        benchmark_returns = returns[benchmark_col]
        investable_cols = [c for c in returns.columns if c != benchmark_col]
    elif benchmark_col:
        print(f"[WARN] Benchmark '{benchmark_col}' absent des données, beta non calculé.")

    if not investable_cols:
        raise ValueError("Aucun actif investissable après retrait du benchmark.")
    investable_returns = returns[investable_cols]
    investable_prices = prices[investable_cols]

    builders: dict[str, Callable[[], PortfolioResult]] = {
        "max_sharpe": lambda: max_sharpe_portfolio(
            investable_returns, risk_free_rate=args.risk_free, allow_short=args.allow_short
        ),
        "minimum_variance": lambda: minimum_variance_portfolio(investable_returns, allow_short=args.allow_short),
        "risk_parity": lambda: risk_parity_portfolio(
            investable_returns, allow_short=args.allow_short, risk_free_rate=args.risk_free
        ),
        "equal_weight": lambda: equal_weight_portfolio(investable_returns, risk_free_rate=args.risk_free),
    }

    portfolio_results: dict[str, PortfolioResult] = {}
    portfolio_returns: dict[str, pd.Series] = {}
    for name, builder in builders.items():
        try:
            result = builder()
            portfolio_results[name] = result
            portfolio_returns[name] = investable_returns @ result.weights
        except RuntimeError as exc:
            print(f"[WARN] Portefeuille '{name}' ignoré: {exc}")

    if not portfolio_results:
        raise RuntimeError("Aucun portefeuille n'a pu être construit.")

    frontier = efficient_frontier(investable_returns, n_points=args.frontier_points, allow_short=args.allow_short)

    market_clock, market_clock_error = _safe_market_clock(args.exchange)

    # Cycles boursiers
    if benchmark_col and benchmark_col in prices.columns:
        index_series = prices[benchmark_col]
    else:
        index_series = investable_prices.mean(axis=1)

    phases = detect_market_cycles(index_series)
    cycle_summary = [
        {
            "start": str(p.start.date()),
            "end": str(p.end.date()),
            "phase": p.phase,
            "duration_days": p.duration_days,
        }
        for p in phases[-8:]
    ]
    dominant_cycles = spectral_cycles(index_series)
    kondratiev = kondratiev_proxy(index_series)

    # Rapport de métriques
    report: dict = {
        "metadata": {
            "source": source,
            "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
            "risk_free_rate_annual": args.risk_free,
            "allow_short": args.allow_short,
            "exchange": args.exchange,
            "market_hours_enforced": enforce_market_sessions,
            "benchmark": benchmark_col or None,
            "assets": investable_cols,
            "market_clock": asdict(market_clock) if market_clock else None,
            "market_clock_warning": market_clock_error,
            "data_warning": data_warning,
        },
        "portfolios": {},
        "market_cycles": {
            "recent_phases": cycle_summary,
            "dominant_cycles_days": dominant_cycles.to_dict(orient="records"),
            "kondratiev_proxy": kondratiev,
        },
    }

    for name, result in portfolio_results.items():
        p_returns = portfolio_returns[name]
        report["portfolios"][name] = {
            "metrics": _compute_metrics(
                p_returns,
                result=result,
                risk_free_rate=args.risk_free,
                benchmark_returns=benchmark_returns,
            ),
            "weights": result.weights.sort_values(ascending=False).to_dict(),
        }

    # Export
    metrics_path = output_dir / "portfolio_report.json"
    weights_path = output_dir / "optimal_weights.csv"
    frontier_path = output_dir / "efficient_frontier.csv"
    returns_path = output_dir / "portfolio_returns.csv"

    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    _portfolio_rows(portfolio_results).to_csv(weights_path, index=False)
    frontier.to_csv(frontier_path, index=False)
    pd.DataFrame(portfolio_returns).to_csv(returns_path)

    # Graphiques
    frontier_img = plot_efficient_frontier(frontier, output_dir)
    corr_img = plot_correlation_heatmap(investable_returns, output_dir)
    compare_img = plot_portfolios_comparison(portfolio_returns, output_dir)
    cycles_img = plot_market_cycle_phases(index_series, phases, output_dir)
    dominant_cycles_img = plot_dominant_cycles(dominant_cycles, output_dir)

    portfolio_graphs: dict[str, str] = {}
    for name, p_returns in portfolio_returns.items():
        portfolio_graphs[name] = str(plot_portfolio_dashboard(name, p_returns, output_dir, window=30))

    backup_target = None
    if not args.no_backup:
        backup_root = Path(args.backup_dir) if args.backup_dir else output_dir / "backups"
        backup_target = _backup_output_files(output_dir, backup_root=backup_root)

    print("=== Analyse terminée ===")
    if data_warning:
        print(f"[WARN] {data_warning}")
    if market_clock_error:
        print(f"[WARN] {market_clock_error}")
    for name in portfolio_results:
        metrics = report["portfolios"][name]["metrics"]
        print(
            f"[{name}] Sharpe={metrics['sharpe']:.4f} | Vol={metrics['volatility_annual']:.4f} "
            f"| Return={metrics['expected_return_annual']:.4f}"
        )
    print(f"Rapport JSON: {metrics_path}")
    print(f"Poids CSV: {weights_path}")
    print(f"Frontière CSV: {frontier_path}")
    print(f"Rendements CSV: {returns_path}")
    print(f"Graphiques globaux: {frontier_img}, {corr_img}, {compare_img}, {cycles_img}, {dominant_cycles_img}")
    print(f"Graphiques par portefeuille: {portfolio_graphs}")
    if backup_target is not None:
        print(f"Backup horodaté: {backup_target}")


if __name__ == "__main__":
    main()
