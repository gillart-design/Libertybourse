from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

EXCHANGE_SESSION_OVERRIDES: dict[str, dict[str, object]] = {
    # Euronext Paris: séance cash standard 09:00 -> 17:30 (heure locale Paris).
    "XPAR": {
        "timezone": "Europe/Paris",
        "open_hour": 9,
        "open_minute": 0,
        "close_hour": 17,
        "close_minute": 30,
    },
    # Trade Republic (Lang & Schwarz Exchange): fenêtre étendue 07:30 -> 23:00 (Berlin).
    "TRLS": {
        "timezone": "Europe/Berlin",
        "open_hour": 7,
        "open_minute": 30,
        "close_hour": 23,
        "close_minute": 0,
        # Fallback robuste: calendrier semaine (lun-ven) si la classe bourse n'existe pas.
        "fallback_weekdays_only": True,
    },
}

EXCHANGE_CALENDAR_ALIASES: dict[str, str] = {
    # Profil Trade Republic piloté sur un calendrier XETR (jours fériés all.),
    # puis bornes horaires forcées via EXCHANGE_SESSION_OVERRIDES.
    "TRLS": "XETR",
}


@dataclass
class MarketData:
    prices: pd.DataFrame
    returns: pd.DataFrame


@dataclass
class MarketClock:
    exchange: str
    timezone: str
    is_open: bool
    next_open_utc: str | None
    next_close_utc: str | None
    current_session_open_utc: str | None
    current_session_close_utc: str | None


def load_prices(csv_path: str | Path) -> pd.DataFrame:
    """
    Charge des prix depuis un CSV.

    Formats acceptés:
    1) Large: Date, AAPL, MSFT, ...
    2) Long: Date, Ticker, Close
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable: {path}")

    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}

    if "date" not in cols:
        raise ValueError("Le CSV doit contenir une colonne Date/date.")

    if "ticker" in cols and "close" in cols:
        date_col = cols["date"]
        ticker_col = cols["ticker"]
        close_col = cols["close"]
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        out = (
            df.dropna(subset=[date_col, ticker_col, close_col])
            .pivot(index=date_col, columns=ticker_col, values=close_col)
            .sort_index()
        )
    else:
        date_col = cols["date"]
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        out = df.set_index(date_col).sort_index()

    out = out.apply(pd.to_numeric, errors="coerce").dropna(how="all").ffill()
    if out.shape[1] < 2:
        raise ValueError("Il faut au moins 2 actifs pour l'optimisation de portefeuille.")

    return out


def compute_returns(prices: pd.DataFrame) -> pd.DataFrame:
    returns = prices.pct_change().replace([np.inf, -np.inf], np.nan).dropna(how="all")
    returns = returns.dropna(axis=1, how="all")
    if returns.empty:
        raise ValueError("Rendements vides après nettoyage.")
    return returns


def parse_tickers(tickers: str | list[str]) -> list[str]:
    if isinstance(tickers, str):
        parsed = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        parsed = [str(t).strip().upper() for t in tickers if str(t).strip()]
    deduped = list(dict.fromkeys(parsed))
    if not deduped:
        raise ValueError("Aucun ticker valide.")
    return deduped


def _extract_close_prices(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("Aucune donnée reçue depuis l'API.")

    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = set(raw.columns.get_level_values(0))
        lvl1 = set(raw.columns.get_level_values(1))
        if "Adj Close" in lvl0:
            close = raw["Adj Close"]
        elif "Close" in lvl0:
            close = raw["Close"]
        elif "Adj Close" in lvl1:
            close = raw.xs("Adj Close", axis=1, level=1)
        elif "Close" in lvl1:
            close = raw.xs("Close", axis=1, level=1)
        else:
            raise ValueError("Colonnes Close/Adj Close absentes dans la réponse API.")
    else:
        if "Adj Close" in raw.columns:
            close = raw[["Adj Close"]].copy()
        elif "Close" in raw.columns:
            close = raw[["Close"]].copy()
        else:
            raise ValueError("Colonne Close/Adj Close absente dans la réponse API.")
        close.columns = [tickers[0]]

    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])

    close = close.apply(pd.to_numeric, errors="coerce").dropna(how="all").sort_index()
    if close.empty:
        raise ValueError("Données de clôture vides après nettoyage.")
    return close


def fetch_yahoo_prices(
    tickers: str | list[str],
    start: str,
    end: str | None = None,
    interval: str = "1d",
    auto_adjust: bool = True,
) -> pd.DataFrame:
    """
    Récupère des prix via Yahoo Finance (API publique yfinance).
    """
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError("Installe yfinance pour utiliser le mode API: pip install yfinance") from exc

    symbols = parse_tickers(tickers)
    raw = yf.download(
        tickers=symbols,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=auto_adjust,
        progress=False,
        group_by="column",
        threads=True,
    )
    close = _extract_close_prices(raw, symbols)

    idx = pd.to_datetime(close.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    close.index = idx
    close = close.dropna(how="all")
    if close.empty:
        raise ValueError("Aucune donnée exploitable reçue de Yahoo Finance.")
    return close


def _get_calendar(exchange: str):
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise ImportError(
            "Installe pandas-market-calendars pour la gestion des horaires de marché: "
            "pip install pandas-market-calendars"
        ) from exc
    exchange_code = str(exchange or "").upper()
    calendar_code = EXCHANGE_CALENDAR_ALIASES.get(exchange_code, exchange_code)
    return mcal.get_calendar(calendar_code)


def _to_utc_series(ts: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(ts, errors="coerce")
    if getattr(parsed.dt, "tz", None) is None:
        return parsed.dt.tz_localize("UTC")
    return parsed.dt.tz_convert("UTC")


def _apply_exchange_session_overrides(schedule: pd.DataFrame, exchange: str) -> pd.DataFrame:
    if schedule is None or schedule.empty:
        return schedule
    cfg = EXCHANGE_SESSION_OVERRIDES.get(str(exchange).upper())
    if not cfg:
        return schedule

    out = schedule.copy()
    open_utc = _to_utc_series(out["market_open"])
    close_utc = _to_utc_series(out["market_close"])
    tz_local = str(cfg["timezone"])
    open_local = open_utc.dt.tz_convert(tz_local)
    close_local = close_utc.dt.tz_convert(tz_local)

    open_anchor = open_local.dt.normalize()
    close_anchor = close_local.dt.normalize()
    open_fixed_local = open_anchor + pd.Timedelta(hours=int(cfg["open_hour"]), minutes=int(cfg["open_minute"]))
    close_fixed_local = close_anchor + pd.Timedelta(hours=int(cfg["close_hour"]), minutes=int(cfg["close_minute"]))

    out["market_open"] = open_fixed_local.dt.tz_convert("UTC")
    out["market_close"] = close_fixed_local.dt.tz_convert("UTC")
    return out


def _build_weekday_schedule(exchange: str, start_date, end_date) -> pd.DataFrame:
    cfg = EXCHANGE_SESSION_OVERRIDES.get(str(exchange).upper(), {})
    tz_local = str(cfg.get("timezone", "UTC"))
    open_hour = int(cfg.get("open_hour", 9))
    open_minute = int(cfg.get("open_minute", 0))
    close_hour = int(cfg.get("close_hour", 17))
    close_minute = int(cfg.get("close_minute", 30))

    days = pd.date_range(start=start_date, end=end_date, freq="D")
    if days.empty:
        return pd.DataFrame(columns=["market_open", "market_close"])
    weekdays = days[days.weekday < 5]
    if len(weekdays) == 0:
        return pd.DataFrame(columns=["market_open", "market_close"])

    anchor_local = weekdays.tz_localize(tz_local)
    open_local = anchor_local + pd.Timedelta(hours=open_hour, minutes=open_minute)
    close_local = anchor_local + pd.Timedelta(hours=close_hour, minutes=close_minute)
    schedule = pd.DataFrame(
        {
            "market_open": open_local.tz_convert("UTC"),
            "market_close": close_local.tz_convert("UTC"),
        },
        index=pd.to_datetime(weekdays).normalize(),
    )
    return schedule


def _load_exchange_schedule(exchange: str, start_date, end_date) -> pd.DataFrame:
    exchange_code = str(exchange or "").upper()
    cfg = EXCHANGE_SESSION_OVERRIDES.get(exchange_code, {})

    if bool(cfg.get("fallback_weekdays_only", False)):
        return _build_weekday_schedule(exchange_code, start_date, end_date)

    try:
        cal = _get_calendar(exchange_code)
        schedule = cal.schedule(start_date=start_date, end_date=end_date)
    except Exception:
        # En dernier recours, si un override existe, on bascule sur un calendrier semaine.
        if cfg:
            return _build_weekday_schedule(exchange_code, start_date, end_date)
        raise
    return _apply_exchange_session_overrides(schedule, exchange_code)


def filter_prices_to_market_sessions(prices: pd.DataFrame, exchange: str = "XNYS") -> pd.DataFrame:
    """
    Filtre les prix aux dates/heures réellement ouvertes pour l'exchange indiqué.
    """
    if prices.empty:
        return prices

    idx = pd.to_datetime(prices.index, errors="coerce")
    if idx.isna().all():
        raise ValueError("Index temporel invalide pour filtrage marché.")

    start_date = idx.min().date()
    end_date = idx.max().date()
    schedule = _load_exchange_schedule(exchange=exchange, start_date=start_date, end_date=end_date)
    if schedule.empty:
        return prices.iloc[0:0]

    has_time_component = bool((idx.hour != 0).any() or (idx.minute != 0).any() or (idx.second != 0).any())
    if not has_time_component:
        sessions = pd.to_datetime(schedule.index).normalize()
        keep = idx.normalize().isin(sessions)
        return prices.loc[keep]

    idx_utc = idx.tz_convert("UTC") if getattr(idx, "tz", None) is not None else idx.tz_localize("UTC")
    idx_naive_utc = idx_utc.tz_localize(None)
    session_key = idx_naive_utc.normalize()
    schedule_local = schedule.copy()
    schedule_local.index = pd.to_datetime(schedule_local.index).normalize()
    opens = session_key.map(schedule_local["market_open"].dt.tz_convert("UTC").dt.tz_localize(None))
    closes = session_key.map(schedule_local["market_close"].dt.tz_convert("UTC").dt.tz_localize(None))
    keep = opens.notna() & closes.notna() & (idx_naive_utc >= opens) & (idx_naive_utc <= closes)
    keep_mask = np.asarray(keep, dtype=bool)
    return prices.loc[keep_mask]


def get_market_clock(exchange: str = "XNYS", now_utc: pd.Timestamp | None = None) -> MarketClock:
    """
    Retourne l'état d'ouverture du marché et les prochaines bornes de session.
    """
    exchange_code = str(exchange or "").upper()
    now = now_utc or pd.Timestamp.now(tz="UTC")
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")

    schedule = _load_exchange_schedule(
        exchange=exchange_code,
        start_date=(now - timedelta(days=7)).date(),
        end_date=(now + timedelta(days=7)).date(),
    )
    tz_name = str(EXCHANGE_SESSION_OVERRIDES.get(exchange_code, {}).get("timezone", ""))
    if not tz_name:
        try:
            tz_name = str(_get_calendar(exchange_code).tz)
        except Exception:
            tz_name = "UTC"
    if schedule.empty:
        return MarketClock(
            exchange=exchange_code,
            timezone=tz_name,
            is_open=False,
            next_open_utc=None,
            next_close_utc=None,
            current_session_open_utc=None,
            current_session_close_utc=None,
        )

    active = schedule[(schedule["market_open"] <= now) & (schedule["market_close"] >= now)]
    future = schedule[schedule["market_open"] > now]

    current_open = active.iloc[0]["market_open"] if not active.empty else None
    current_close = active.iloc[0]["market_close"] if not active.empty else None
    next_open = current_open if current_open is not None else (future.iloc[0]["market_open"] if not future.empty else None)
    next_close = current_close if current_close is not None else (future.iloc[0]["market_close"] if not future.empty else None)

    return MarketClock(
        exchange=exchange_code,
        timezone=tz_name,
        is_open=not active.empty,
        next_open_utc=next_open.isoformat() if next_open is not None else None,
        next_close_utc=next_close.isoformat() if next_close is not None else None,
        current_session_open_utc=current_open.isoformat() if current_open is not None else None,
        current_session_close_utc=current_close.isoformat() if current_close is not None else None,
    )


def market_data_from_prices(prices: pd.DataFrame, exchange: str | None = None, enforce_market_sessions: bool = False) -> MarketData:
    cleaned_prices = prices.copy()
    if enforce_market_sessions and exchange:
        cleaned_prices = filter_prices_to_market_sessions(cleaned_prices, exchange=exchange)

    returns = compute_returns(cleaned_prices)
    common_cols = [c for c in cleaned_prices.columns if c in returns.columns]
    return MarketData(prices=cleaned_prices[common_cols], returns=returns[common_cols])


def load_market_data(csv_path: str | Path, exchange: str | None = None, enforce_market_sessions: bool = False) -> MarketData:
    prices = load_prices(csv_path)
    return market_data_from_prices(prices, exchange=exchange, enforce_market_sessions=enforce_market_sessions)


def load_api_market_data(
    tickers: str | list[str],
    start: str,
    end: str | None = None,
    interval: str = "1d",
    exchange: str | None = None,
    enforce_market_sessions: bool = True,
) -> MarketData:
    prices = fetch_yahoo_prices(tickers=tickers, start=start, end=end, interval=interval)
    data = market_data_from_prices(prices=prices, exchange=None, enforce_market_sessions=False)
    if enforce_market_sessions and exchange:
        data = market_data_from_prices(prices=data.prices, exchange=exchange, enforce_market_sessions=True)
    return data


def generate_synthetic_prices(
    n_assets: int = 6,
    n_days: int = 1200,
    seed: int = 42,
    start: str = "2018-01-01",
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n_days)

    # Corrélation factorielle simple
    market = rng.normal(0.0003, 0.01, size=n_days)
    prices = {}
    for i in range(n_assets):
        alpha = rng.normal(0.0001, 0.0001)
        beta = rng.uniform(0.6, 1.4)
        epsilon = rng.normal(0.0, rng.uniform(0.006, 0.02), size=n_days)
        r = alpha + beta * market + epsilon
        s = 100 * np.cumprod(1 + r)
        prices[f"ASSET_{i+1}"] = s

    return pd.DataFrame(prices, index=dates)
