"""Fonctions utilitaires partagées."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import numpy as np
import pandas as pd

from simulator.constants import (
    DEFAULT_BASE_CURRENCY, DISPLAY_TZ, LOG_PATH, TICKER_SUFFIX_CURRENCY,
)

LOGGER = logging.getLogger("portfolio_simulator")


def setup_logger() -> None:
    if LOGGER.handlers:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def coerce_float(value: object, default: float = np.nan) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float, np.integer, np.floating)):
        try:
            out = float(value)
            return out if np.isfinite(out) else default
        except Exception:
            return default
    raw = str(value).strip()
    if not raw:
        return default
    cleaned = raw.replace("\u202f", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        out = float(cleaned)
        return out if np.isfinite(out) else default
    except Exception:
        return default


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def to_display_time(value: str | None) -> str:
    if not value:
        return "N/A"
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")


def eur(amount: float) -> str:
    return f"{amount:,.2f} €".replace(",", " ").replace(".", ",")


def pct(value: float) -> str:
    return f"{value:+.2f}%"


def money(amount: float, currency: str) -> str:
    cur = (currency or DEFAULT_BASE_CURRENCY).upper()
    symbol_map = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "CHF": "CHF", "HKD": "HK$"}
    s = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    symbol = symbol_map.get(cur, cur)
    return f"{s} {symbol}"


def epoch_to_iso(value: int | float | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        return None


def any_epoch_to_iso(value: int | float | None) -> str:
    if value is None:
        return utc_now_iso()
    raw = float(value)
    if raw > 1e17:
        raw /= 1_000_000_000
    elif raw > 1e14:
        raw /= 1_000_000
    elif raw > 1e11:
        raw /= 1_000
    return datetime.fromtimestamp(raw, tz=timezone.utc).replace(microsecond=0).isoformat()


def polygon_symbol_supported(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}", symbol.upper()))


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def infer_currency(symbol: str, quote_currency: str | None, base_currency: str) -> str:
    """Infère la devise d'un ticker à partir de son suffixe ou de la réponse API.

    CORRIGÉ : gestion de tous les suffixes de l'univers d'actifs (.DE, .SW, .L, .KS, .TW, etc.)
    """
    if quote_currency:
        c = quote_currency.strip().upper()
        if c:
            return c
    s = symbol.upper()
    # Tester les suffixes du plus long au plus court pour éviter les ambiguïtés
    for suffix, cur in sorted(TICKER_SUFFIX_CURRENCY.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return cur
    # Pas de suffixe reconnu → actif US par défaut
    return "USD"


def parse_symbols_csv(raw: str, allowed: set[str] | None = None) -> list[str]:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    deduped = list(dict.fromkeys(symbols))
    if allowed is not None:
        deduped = [s for s in deduped if s in allowed]
    return deduped


def symbols_to_csv(symbols: list[str]) -> str:
    return ",".join(list(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()])))
