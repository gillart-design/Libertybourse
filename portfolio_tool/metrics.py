from __future__ import annotations

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def annualized_return(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    mean_daily = returns.mean()
    return (1 + mean_daily) ** periods - 1


def annualized_volatility(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    return returns.std(ddof=1) * np.sqrt(periods)


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0, periods: int = TRADING_DAYS) -> float:
    rf_periodic = (1 + risk_free_rate) ** (1 / periods) - 1
    excess = returns - rf_periodic
    vol = excess.std(ddof=1)
    if vol == 0 or np.isnan(vol):
        return np.nan
    return (excess.mean() / vol) * np.sqrt(periods)


def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.0, periods: int = TRADING_DAYS) -> float:
    rf_periodic = (1 + risk_free_rate) ** (1 / periods) - 1
    excess = returns - rf_periodic
    downside = excess[excess < 0]
    dd = downside.std(ddof=1)
    if dd == 0 or np.isnan(dd):
        return np.nan
    return (excess.mean() / dd) * np.sqrt(periods)


def max_drawdown(portfolio_returns: pd.Series) -> float:
    wealth = (1 + portfolio_returns).cumprod()
    peak = wealth.cummax()
    dd = wealth / peak - 1
    return float(dd.min())


def var_cvar(returns: pd.Series, alpha: float = 0.95) -> tuple[float, float]:
    q = np.quantile(returns, 1 - alpha)
    tail = returns[returns <= q]
    cvar = tail.mean() if len(tail) else q
    return float(q), float(cvar)


def beta_to_benchmark(asset_returns: pd.Series, bench_returns: pd.Series) -> float:
    aligned = pd.concat([asset_returns, bench_returns], axis=1).dropna()
    if aligned.empty:
        return np.nan
    cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1], ddof=1)
    bench_var = cov[1, 1]
    if bench_var == 0:
        return np.nan
    return cov[0, 1] / bench_var


def rolling_vol_and_variance(returns: pd.Series, window: int = 30, periods: int = TRADING_DAYS) -> pd.DataFrame:
    # Formule de variance empirique: Var(X)=1/(n-1)*sum((x_i-mean(x))^2)
    rolling_var = returns.rolling(window).var(ddof=1)
    rolling_vol = np.sqrt(rolling_var) * np.sqrt(periods)
    return pd.DataFrame(
        {
            "rolling_variance": rolling_var,
            "rolling_annualized_volatility": rolling_vol,
        }
    )
