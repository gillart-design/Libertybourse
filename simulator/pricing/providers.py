"""Fournisseurs de cotations avec gestion de fraîcheur.

CORRECTIONS :
- infer_currency utilise le mapping complet
- fetch_execution_price : fonction dédiée non cachée pour l'exécution d'ordres
- fetch_realtime_quotes : enrichit avec un champ freshness_tier
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import streamlit as st

from simulator.constants import (
    API_BACKOFF_BASE_SECONDS, API_MAX_RETRIES, API_CIRCUIT_BREAKER_ERRORS,
    API_CIRCUIT_BREAKER_SECONDS, DEFAULT_BASE_CURRENCY,
    PROVIDER_HEALTH, PROVIDER_HEALTH_LOCK, PROVIDER_LAST_CALL_TS,
    PROVIDER_MIN_INTERVAL_SECONDS, PROVIDER_RATE_LOCK,
    SSL_ERROR_MARKERS,
)
from simulator.helpers import (
    safe_float, utc_now_iso, epoch_to_iso, any_epoch_to_iso,
    infer_currency, chunked, polygon_symbol_supported, LOGGER,
)

QUOTE_COLUMNS = [
    "symbol", "last", "previous", "change_pct", "quote_time_utc",
    "market_state", "currency", "source", "regular_price", "pre_price",
    "post_price", "official_close", "price_context", "api_error", "freshness_tier",
]


def _provider_record_success(provider: str) -> None:
    with PROVIDER_HEALTH_LOCK:
        h = PROVIDER_HEALTH.setdefault(provider, {})
        h["success"] = float(h.get("success", 0.0)) + 1.0
        h["consecutive_error"] = 0.0


def _provider_record_error(provider: str, message: str) -> None:
    with PROVIDER_HEALTH_LOCK:
        h = PROVIDER_HEALTH.setdefault(provider, {})
        h["error"] = float(h.get("error", 0.0)) + 1.0
        h["consecutive_error"] = float(h.get("consecutive_error", 0.0)) + 1.0
        h["last_error"] = message[:350]
        h["last_error_utc"] = utc_now_iso()
        if float(h.get("consecutive_error", 0.0)) >= API_CIRCUIT_BREAKER_ERRORS:
            h["circuit_open_until"] = time.monotonic() + API_CIRCUIT_BREAKER_SECONDS


def _provider_circuit_open(provider: str) -> bool:
    with PROVIDER_HEALTH_LOCK:
        open_until = float(PROVIDER_HEALTH.get(provider, {}).get("circuit_open_until", 0.0))
    return open_until > time.monotonic()


def _provider_open_circuit(provider: str, seconds: float) -> None:
    with PROVIDER_HEALTH_LOCK:
        h = PROVIDER_HEALTH.setdefault(provider, {})
        h["circuit_open_until"] = max(float(h.get("circuit_open_until", 0.0)), time.monotonic() + max(seconds, 1.0))


def _provider_wait_for_rate_limit(provider: str) -> None:
    min_interval = float(PROVIDER_MIN_INTERVAL_SECONDS.get(provider, 0.0))
    if min_interval <= 0:
        return
    with PROVIDER_RATE_LOCK:
        now = time.monotonic()
        last = float(PROVIDER_LAST_CALL_TS.get(provider, 0.0))
        wait = (last + min_interval) - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        PROVIDER_LAST_CALL_TS[provider] = now


def _is_ssl_transport_error(message: str) -> bool:
    msg = str(message or "").upper()
    return any(marker in msg for marker in SSL_ERROR_MARKERS)


def _http_get_json_with_resilience(url: str, provider: str, timeout: int = 8) -> tuple[dict | None, str | None]:
    if _provider_circuit_open(provider):
        return None, "circuit_open"
    last_error = ""
    for attempt in range(API_MAX_RETRIES):
        try:
            _provider_wait_for_rate_limit(provider)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            _provider_record_success(provider)
            return payload, None
        except Exception as exc:
            last_error = str(exc)
            _provider_record_error(provider, last_error)
            if _is_ssl_transport_error(last_error):
                _provider_open_circuit(provider, API_CIRCUIT_BREAKER_SECONDS)
                return None, f"ssl_error:{last_error}"
            if attempt < API_MAX_RETRIES - 1:
                time.sleep(API_BACKOFF_BASE_SECONDS * (2 ** attempt))
    return None, last_error or "network_error"


def _safe_yf_import():
    try:
        import yfinance as yf
        return yf
    except Exception:
        return None


@st.cache_data(ttl=120, show_spinner=False)
def fetch_quotes_daily(symbols: tuple[str, ...]) -> pd.DataFrame:
    """Fallback : récupère les close journaliers via yfinance history.
    Marqué freshness_tier=daily_close.
    """
    yf = _safe_yf_import()
    if not yf or not symbols:
        return pd.DataFrame(columns=QUOTE_COLUMNS)
    rows = []
    for symbol in symbols:
        try:
            hist = yf.Ticker(symbol).history(period="10d", interval="1d", auto_adjust=True)
            close = hist["Close"].dropna()
            if close.empty:
                continue
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else last
            change_pct = ((last / prev) - 1) * 100 if prev else 0.0
            rows.append({
                "symbol": symbol, "last": last, "previous": prev,
                "change_pct": change_pct, "quote_time_utc": utc_now_iso(),
                "market_state": "DELAYED", "currency": "",
                "source": "yfinance_history",
                "regular_price": last, "pre_price": 0.0, "post_price": 0.0,
                "official_close": prev, "price_context": "daily_close",
                "api_error": "", "freshness_tier": "daily_close",
            })
            _provider_record_success("yfinance_history")
        except Exception as exc:
            LOGGER.warning("fetch_quotes_daily %s: %s", symbol, exc)
            continue
    return pd.DataFrame(rows, columns=QUOTE_COLUMNS)


@st.cache_data(ttl=5, show_spinner=False)
def fetch_realtime_quotes(symbols: tuple[str, ...]) -> pd.DataFrame:
    """Cotations quasi temps réel via Yahoo v7 Quote API + fallback daily."""
    cleaned = tuple(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()]))
    if not cleaned:
        return pd.DataFrame(columns=QUOTE_COLUMNS)

    rows_by_symbol: dict[str, dict] = {}
    symbol_errors: dict[str, str] = {}
    for part in chunked(list(cleaned), 50):
        joined = ",".join(part)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={urllib.parse.quote(joined)}"
        payload, err = _http_get_json_with_resilience(url, provider="yahoo_quote_api", timeout=8)
        if err:
            for s in part:
                symbol_errors[s] = f"yahoo_quote_api:{err}"
            quotes = []
        else:
            quotes = (payload or {}).get("quoteResponse", {}).get("result", [])

        for q in quotes:
            symbol = str(q.get("symbol", "")).upper()
            if not symbol:
                continue
            market_state = str(q.get("marketState", "UNKNOWN"))
            regular_price = q.get("regularMarketPrice")
            pre_price = q.get("preMarketPrice")
            post_price = q.get("postMarketPrice")
            previous = q.get("regularMarketPreviousClose")
            last = regular_price
            if previous is None:
                previous = q.get("regularMarketOpen") or last
            price_context = "regular"
            freshness = "realtime"
            if market_state.upper().startswith("PRE") and pre_price is not None:
                last = pre_price
                price_context = "pre"
            elif market_state.upper().startswith("POST") and post_price is not None:
                last = post_price
                price_context = "post"
            elif last is None:
                last = post_price if post_price is not None else pre_price
                price_context = "off_session"
                freshness = "delayed"
            if last is None:
                last = q.get("bid")
                price_context = "bid_fallback"
                freshness = "delayed"
            if last is None:
                continue
            change_pct = q.get("regularMarketChangePercent")
            if change_pct is None and previous not in (None, 0):
                change_pct = (float(last) / float(previous) - 1) * 100
            quote_time = epoch_to_iso(
                q.get("regularMarketTime") or q.get("postMarketTime") or q.get("preMarketTime")
            ) or utc_now_iso()
            rows_by_symbol[symbol] = {
                "symbol": symbol,
                "last": float(last),
                "previous": float(previous) if previous is not None else float(last),
                "change_pct": float(change_pct or 0.0),
                "quote_time_utc": quote_time,
                "market_state": market_state,
                "currency": str(q.get("currency", "")),
                "source": "yahoo_quote_api",
                "regular_price": safe_float(regular_price, float(last)),
                "pre_price": safe_float(pre_price, 0.0),
                "post_price": safe_float(post_price, 0.0),
                "official_close": safe_float(previous, float(last)),
                "price_context": price_context,
                "api_error": "",
                "freshness_tier": freshness,
            }
            symbol_errors.pop(symbol, None)

    # Fallback sur daily pour les symboles manquants
    missing = [s for s in cleaned if s not in rows_by_symbol]
    if missing:
        fallback = fetch_quotes_daily(tuple(missing))
        if not fallback.empty:
            for row in fallback.itertuples(index=False):
                sym = str(row.symbol).upper()
                rows_by_symbol[sym] = {c: getattr(row, c, None) for c in QUOTE_COLUMNS}
                symbol_errors.pop(sym, None)

    # Symboles toujours non résolus
    for sym in [s for s in cleaned if s not in rows_by_symbol]:
        rows_by_symbol[sym] = {
            "symbol": sym, "last": np.nan, "previous": np.nan,
            "change_pct": np.nan, "quote_time_utc": utc_now_iso(),
            "market_state": "UNAVAILABLE", "currency": "",
            "source": "unavailable", "regular_price": np.nan,
            "pre_price": np.nan, "post_price": np.nan,
            "official_close": np.nan, "price_context": "unavailable",
            "api_error": symbol_errors.get(sym, "quote_unavailable"),
            "freshness_tier": "unavailable",
        }

    if not rows_by_symbol:
        return pd.DataFrame(columns=QUOTE_COLUMNS)
    return pd.DataFrame(rows_by_symbol.values()).sort_values("symbol").reset_index(drop=True)


def fetch_execution_price(symbol: str, polygon_api_key: str = "") -> dict:
    """Prix dédié à l'exécution d'un ordre — JAMAIS CACHÉ.

    Retourne un dict avec 'price', 'currency', 'source', 'timestamp', 'freshness_tier'.
    Si le prix n'est pas disponible ou trop ancien, 'price' est NaN.
    """
    symbol = symbol.strip().upper()
    result = {
        "price": np.nan,
        "currency": "",
        "source": "unavailable",
        "timestamp": utc_now_iso(),
        "freshness_tier": "unavailable",
        "age_seconds": float("inf"),
    }

    # Tentative 1 : Polygon snapshot (si clé dispo et ticker US)
    if polygon_api_key and polygon_symbol_supported(symbol):
        url = (
            f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            f"?tickers={urllib.parse.quote(symbol)}&apiKey={urllib.parse.quote(polygon_api_key)}"
        )
        payload, err = _http_get_json_with_resilience(url, provider="polygon_ws_tick", timeout=5)
        if not err and payload:
            tickers = payload.get("tickers", [])
            if tickers:
                item = tickers[0]
                last_trade = item.get("lastTrade") or {}
                price = safe_float(last_trade.get("p"), np.nan)
                if not np.isnan(price) and price > 0:
                    ts_iso = any_epoch_to_iso(last_trade.get("t")) if last_trade.get("t") else utc_now_iso()
                    ts = pd.Timestamp(ts_iso)
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    age = (pd.Timestamp.now(tz="UTC") - ts).total_seconds()
                    result = {
                        "price": price, "currency": "USD",
                        "source": "polygon_snapshot", "timestamp": ts_iso,
                        "freshness_tier": "realtime" if age < 60 else "delayed",
                        "age_seconds": age,
                    }
                    if age < 120:  # Acceptable pour exécution
                        return result

    # Tentative 2 : Yahoo Quote API (pas de cache)
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={urllib.parse.quote(symbol)}"
    payload, err = _http_get_json_with_resilience(url, provider="yahoo_quote_api", timeout=6)
    if not err and payload:
        quotes = payload.get("quoteResponse", {}).get("result", [])
        if quotes:
            q = quotes[0]
            price = safe_float(q.get("regularMarketPrice"), np.nan)
            if np.isnan(price):
                price = safe_float(q.get("postMarketPrice"), safe_float(q.get("preMarketPrice"), np.nan))
            if not np.isnan(price) and price > 0:
                ts_raw = q.get("regularMarketTime") or q.get("postMarketTime") or q.get("preMarketTime")
                ts_iso = epoch_to_iso(ts_raw) or utc_now_iso()
                ts = pd.Timestamp(ts_iso)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                age = (pd.Timestamp.now(tz="UTC") - ts).total_seconds()
                result = {
                    "price": price, "currency": str(q.get("currency", "")),
                    "source": "yahoo_quote_api", "timestamp": ts_iso,
                    "freshness_tier": "realtime" if age < 120 else "delayed",
                    "age_seconds": age,
                }

    return result


def merge_quotes(primary: pd.DataFrame, secondary: pd.DataFrame, symbols: tuple[str, ...]) -> pd.DataFrame:
    """Fusionne cotations en priorisant le primary."""
    parts = []
    if primary is not None and not primary.empty:
        parts.append(primary.copy())
    if secondary is not None and not secondary.empty:
        parts.append(secondary.copy())
    if not parts:
        return pd.DataFrame(columns=QUOTE_COLUMNS)
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["symbol"], keep="first")
    order = {s: i for i, s in enumerate(symbols)}
    merged["order"] = merged["symbol"].map(order).fillna(10_000)
    return merged.sort_values(["order", "symbol"]).drop(columns=["order"]).reset_index(drop=True)
