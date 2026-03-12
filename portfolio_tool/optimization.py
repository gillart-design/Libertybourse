from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .metrics import TRADING_DAYS


@dataclass
class PortfolioResult:
    weights: pd.Series
    expected_return: float
    volatility: float
    sharpe: float


def _portfolio_stats(weights: np.ndarray, mean_returns: np.ndarray, cov: np.ndarray, rf: float = 0.0) -> tuple[float, float, float]:
    exp_ret = float(np.dot(weights, mean_returns) * TRADING_DAYS)
    vol = float(np.sqrt(weights.T @ (cov * TRADING_DAYS) @ weights))
    sharpe = (exp_ret - rf) / vol if vol > 0 else np.nan
    return exp_ret, vol, sharpe


def _constraints_sum_1(weights: np.ndarray) -> float:
    return np.sum(weights) - 1


def _bounds(n_assets: int, allow_short: bool):
    return None if allow_short else tuple((0.0, 1.0) for _ in range(n_assets))


def portfolio_result_from_weights(
    weights: pd.Series | np.ndarray,
    returns: pd.DataFrame,
    risk_free_rate: float = 0.0,
) -> PortfolioResult:
    if isinstance(weights, pd.Series):
        w = weights.reindex(returns.columns).fillna(0.0).values
    else:
        w = np.array(weights, dtype=float)
    mean_returns = returns.mean().values
    cov = returns.cov().values
    exp_ret, vol, sharpe = _portfolio_stats(w, mean_returns, cov, rf=risk_free_rate)
    return PortfolioResult(weights=pd.Series(w, index=returns.columns), expected_return=exp_ret, volatility=vol, sharpe=sharpe)


def max_sharpe_portfolio(returns: pd.DataFrame, risk_free_rate: float = 0.0, allow_short: bool = False) -> PortfolioResult:
    n = returns.shape[1]
    mean_returns = returns.mean().values
    cov = returns.cov().values

    bounds = _bounds(n, allow_short)
    x0 = np.ones(n) / n

    def objective(w: np.ndarray) -> float:
        _, vol, sharpe = _portfolio_stats(w, mean_returns, cov, rf=risk_free_rate)
        if np.isnan(sharpe):
            return 1e6
        return -sharpe

    cons = ({"type": "eq", "fun": _constraints_sum_1},)
    result = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons)
    if not result.success:
        raise RuntimeError(f"Optimisation Sharpe échouée: {result.message}")

    w = result.x
    exp_ret, vol, sharpe = _portfolio_stats(w, mean_returns, cov, rf=risk_free_rate)
    return PortfolioResult(pd.Series(w, index=returns.columns), exp_ret, vol, sharpe)


def minimum_variance_portfolio(returns: pd.DataFrame, allow_short: bool = False) -> PortfolioResult:
    n = returns.shape[1]
    mean_returns = returns.mean().values
    cov = returns.cov().values

    x0 = np.ones(n) / n
    bounds = _bounds(n, allow_short)
    cons = ({"type": "eq", "fun": _constraints_sum_1},)

    def objective(w: np.ndarray) -> float:
        return float(np.sqrt(w.T @ (cov * TRADING_DAYS) @ w))

    result = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons)
    if not result.success:
        raise RuntimeError(f"Optimisation minimum variance échouée: {result.message}")

    w = result.x
    exp_ret, vol, sharpe = _portfolio_stats(w, mean_returns, cov)
    return PortfolioResult(pd.Series(w, index=returns.columns), exp_ret, vol, sharpe)


def equal_weight_portfolio(returns: pd.DataFrame, risk_free_rate: float = 0.0) -> PortfolioResult:
    n = returns.shape[1]
    weights = np.ones(n) / n
    return portfolio_result_from_weights(weights=weights, returns=returns, risk_free_rate=risk_free_rate)


def risk_parity_portfolio(
    returns: pd.DataFrame,
    allow_short: bool = False,
    risk_free_rate: float = 0.0,
) -> PortfolioResult:
    """
    Approximation risk parity: contributions au risque égales.
    """
    n = returns.shape[1]
    cov = returns.cov().values * TRADING_DAYS
    x0 = np.ones(n) / n
    bounds = _bounds(n, allow_short)
    cons = ({"type": "eq", "fun": _constraints_sum_1},)

    def objective(w: np.ndarray) -> float:
        vol = np.sqrt(w.T @ cov @ w)
        if vol <= 0:
            return 1e6
        marginal = cov @ w
        risk_contrib = (w * marginal) / vol
        target = vol / n
        return float(np.sum((risk_contrib - target) ** 2))

    result = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons)
    if not result.success:
        raise RuntimeError(f"Optimisation risk parity échouée: {result.message}")

    return portfolio_result_from_weights(weights=result.x, returns=returns, risk_free_rate=risk_free_rate)


def min_vol_portfolio_for_target(
    returns: pd.DataFrame,
    target_return: float,
    allow_short: bool = False,
) -> PortfolioResult:
    n = returns.shape[1]
    mean_returns = returns.mean().values
    cov = returns.cov().values

    bounds = _bounds(n, allow_short)
    x0 = np.ones(n) / n

    def portfolio_vol(w: np.ndarray) -> float:
        _, vol, _ = _portfolio_stats(w, mean_returns, cov)
        return vol

    cons = (
        {"type": "eq", "fun": _constraints_sum_1},
        {"type": "eq", "fun": lambda w: float(np.dot(w, mean_returns) * TRADING_DAYS - target_return)},
    )

    result = minimize(portfolio_vol, x0, method="SLSQP", bounds=bounds, constraints=cons)
    if not result.success:
        raise RuntimeError(f"Optimisation min-vol échouée: {result.message}")

    w = result.x
    exp_ret, vol, sharpe = _portfolio_stats(w, mean_returns, cov)
    return PortfolioResult(pd.Series(w, index=returns.columns), exp_ret, vol, sharpe)


def efficient_frontier(
    returns: pd.DataFrame,
    n_points: int = 30,
    allow_short: bool = False,
) -> pd.DataFrame:
    annual_means = returns.mean() * TRADING_DAYS
    min_ret = float(annual_means.min())
    max_ret = float(annual_means.max())
    targets = np.linspace(min_ret, max_ret, n_points)

    rows = []
    for target in targets:
        try:
            p = min_vol_portfolio_for_target(returns, target_return=float(target), allow_short=allow_short)
            rows.append(
                {
                    "target_return": target,
                    "expected_return": p.expected_return,
                    "volatility": p.volatility,
                    "sharpe": p.sharpe,
                }
            )
        except RuntimeError:
            continue

    if not rows:
        return pd.DataFrame(columns=["target_return", "expected_return", "volatility", "sharpe"])
    return pd.DataFrame(rows)
