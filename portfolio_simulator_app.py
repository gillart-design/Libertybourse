from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import sqlite3
import smtplib
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
import hashlib
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from portfolio_tool.data import get_market_clock

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover - fallback runtime
    st_autorefresh = None


APP_TITLE = "Simulateur de Portefeuille Boursier"
APP_SUBTITLE = "Suivi dynamique, répartition géographique/sectorielle et assistant d'aide à la décision"
DEFAULT_INITIAL_CAPITAL = 100_000.0
DEFAULT_EXCHANGE = "XNYS"
DEFAULT_REFRESH_SECONDS = 10
DEFAULT_REALTIME_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "GLD", "EEM"]
DEFAULT_LIVE_MODE = "polling"
DEFAULT_BASE_CURRENCY = "EUR"
DEFAULT_ACCOUNTING_METHOD = "fifo"
DEFAULT_SNAPSHOT_MIN_SECONDS = 10
DEFAULT_SNAPSHOT_MIN_DELTA = 1.0
DEFAULT_WS_STALE_SECONDS = 20
DEFAULT_MAX_LINE_PCT = 25.0
DEFAULT_MAX_SECTOR_PCT = 45.0
DEFAULT_MAX_ZONE_PCT = 55.0
DEFAULT_ALERT_LOSS_PCT = -7.0
DEFAULT_ALERT_DRAWDOWN_PCT = -10.0
DEFAULT_ALERT_GAIN_PCT = 10.0
DISPLAY_TZ = ZoneInfo("Europe/Paris")
DB_PATH = Path("data/portfolio_simulator.db")


def user_db_path(user_email: str) -> Path:
    """Return a per-user sqlite DB path (stable, private) based on a hash of the user email."""
    base_dir = Path("data/users")
    base_dir.mkdir(parents=True, exist_ok=True)
    safe_user = hashlib.sha256(user_email.strip().lower().encode("utf-8")).hexdigest()[:24]
    return base_dir / f"{safe_user}.db"

LOG_PATH = Path("output/portfolio_app.log")
ALERT_COOLDOWN_SECONDS = 600

EVENT_COLORS = {
    "INIT": "#9ca3af",
    "BUY": "#2563eb",
    "SELL": "#ef4444",
    "UP": "#16a34a",
    "DOWN": "#dc2626",
}

COUNTRY_TO_ZONE = {
    "United States": "USA",
    "USA": "USA",
    "France": "Europe",
    "Germany": "Europe",
    "United Kingdom": "Europe",
    "Switzerland": "Europe",
    "Netherlands": "Europe",
    "Italy": "Europe",
    "Spain": "Europe",
    "China": "Asie",
    "Japan": "Asie",
    "South Korea": "Asie",
    "Hong Kong": "Asie",
    "Taiwan": "Asie",
    "India": "Pays émergent",
    "Brazil": "Pays émergent",
    "Mexico": "Pays émergent",
    "Indonesia": "Pays émergent",
    "South Africa": "Pays émergent",
    "Turkey": "Pays émergent",
    "Chile": "Pays émergent",
    "Peru": "Pays émergent",
}

RISK_KEYWORDS = {
    "war": 4,
    "guerre": 4,
    "sanction": 3,
    "conflit": 3,
    "conflict": 3,
    "tariff": 2,
    "douane": 2,
    "attack": 3,
    "attaque": 3,
    "embargo": 3,
    "oil": 1,
    "petrol": 1,
    "taiwan": 2,
    "middle east": 2,
    "ukraine": 2,
}


ASSET_UNIVERSE = [
    {"symbol": "AAPL", "name": "Apple", "asset_type": "Action", "zone": "USA", "sector": "Technologie"},
    {"symbol": "MSFT", "name": "Microsoft", "asset_type": "Action", "zone": "USA", "sector": "Technologie"},
    {"symbol": "NVDA", "name": "NVIDIA", "asset_type": "Action", "zone": "USA", "sector": "Semi-conducteurs"},
    {"symbol": "JPM", "name": "JPMorgan", "asset_type": "Action", "zone": "USA", "sector": "Finance"},
    {"symbol": "SPY", "name": "S&P 500 ETF", "asset_type": "ETF", "zone": "USA", "sector": "Indice large cap"},
    {"symbol": "QQQ", "name": "Nasdaq 100 ETF", "asset_type": "ETF", "zone": "USA", "sector": "Technologie"},
    {"symbol": "MC.PA", "name": "LVMH", "asset_type": "Action", "zone": "Europe", "sector": "Luxe"},
    {"symbol": "SAN.PA", "name": "Sanofi", "asset_type": "Action", "zone": "Europe", "sector": "Santé"},
    {"symbol": "ASML.AS", "name": "ASML", "asset_type": "Action", "zone": "Europe", "sector": "Semi-conducteurs"},
    {"symbol": "IEUR", "name": "MSCI Europe ETF", "asset_type": "ETF", "zone": "Europe", "sector": "Indice Europe"},
    {"symbol": "EWQ", "name": "MSCI France ETF", "asset_type": "ETF", "zone": "Europe", "sector": "Indice France"},
    {"symbol": "7203.T", "name": "Toyota", "asset_type": "Action", "zone": "Asie", "sector": "Automobile"},
    {"symbol": "SONY", "name": "Sony", "asset_type": "Action", "zone": "Asie", "sector": "Technologie"},
    {"symbol": "9988.HK", "name": "Alibaba", "asset_type": "Action", "zone": "Asie", "sector": "E-commerce"},
    {"symbol": "EWJ", "name": "MSCI Japan ETF", "asset_type": "ETF", "zone": "Asie", "sector": "Indice Japon"},
    {"symbol": "MCHI", "name": "MSCI China ETF", "asset_type": "ETF", "zone": "Asie", "sector": "Indice Chine"},
    {"symbol": "INFY", "name": "Infosys", "asset_type": "Action", "zone": "Pays émergent", "sector": "Technologie"},
    {"symbol": "VALE", "name": "Vale", "asset_type": "Action", "zone": "Pays émergent", "sector": "Matières premières"},
    {"symbol": "NIO", "name": "NIO", "asset_type": "Action", "zone": "Pays émergent", "sector": "Mobilité électrique"},
    {"symbol": "EEM", "name": "MSCI Emerging Markets ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice EM"},
    {"symbol": "VWO", "name": "FTSE Emerging Markets ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice EM"},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "asset_type": "Métal précieux", "zone": "USA", "sector": "Or"},
    {"symbol": "IAU", "name": "iShares Gold Trust", "asset_type": "Métal précieux", "zone": "USA", "sector": "Or"},
    {"symbol": "SLV", "name": "iShares Silver Trust", "asset_type": "Métal précieux", "zone": "USA", "sector": "Argent"},
    {"symbol": "PPLT", "name": "abrdn Physical Platinum", "asset_type": "Métal précieux", "zone": "USA", "sector": "Platine"},
    {"symbol": "PALL", "name": "abrdn Physical Palladium", "asset_type": "Métal précieux", "zone": "USA", "sector": "Palladium"},
    {"symbol": "REMX", "name": "Rare Earth / Strategic Metals ETF", "asset_type": "Terres rares", "zone": "USA", "sector": "Métaux stratégiques"},
    {"symbol": "LIT", "name": "Lithium & Battery Tech ETF", "asset_type": "Terres rares", "zone": "USA", "sector": "Lithium"},
]

CATALOG_BY_SYMBOL = {row["symbol"]: row for row in ASSET_UNIVERSE}

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


def parse_symbols_csv(raw: str, allowed: set[str] | None = None) -> list[str]:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    deduped = list(dict.fromkeys(symbols))
    if allowed is not None:
        deduped = [s for s in deduped if s in allowed]
    return deduped


def symbols_to_csv(symbols: list[str]) -> str:
    return ",".join(list(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()])))


def default_mode_settings(allowed_symbols: list[str]) -> dict[str, str]:
    allowed = {s.strip().upper() for s in allowed_symbols if s and s.strip()}
    symbols = [s for s in DEFAULT_REALTIME_SYMBOLS if s in allowed]
    if not symbols and allowed:
        symbols = sorted(allowed)[:6]
    if not symbols:
        symbols = DEFAULT_REALTIME_SYMBOLS.copy()
    return {
        "live_enabled": "1",
        "live_mode": "polling",
        "refresh_seconds": str(DEFAULT_REFRESH_SECONDS),
        "realtime_symbols": symbols_to_csv(symbols),
        "snapshot_min_seconds": str(DEFAULT_SNAPSHOT_MIN_SECONDS),
        "snapshot_min_delta": str(DEFAULT_SNAPSHOT_MIN_DELTA),
        "ws_stale_seconds": str(DEFAULT_WS_STALE_SECONDS),
        "max_line_pct": str(DEFAULT_MAX_LINE_PCT),
        "max_sector_pct": str(DEFAULT_MAX_SECTOR_PCT),
        "max_zone_pct": str(DEFAULT_MAX_ZONE_PCT),
        "alert_loss_pct": str(DEFAULT_ALERT_LOSS_PCT),
        "alert_drawdown_pct": str(DEFAULT_ALERT_DRAWDOWN_PCT),
        "alert_gain_pct": str(DEFAULT_ALERT_GAIN_PCT),
    }


def apply_default_mode_settings(conn: sqlite3.Connection, allowed_symbols: list[str]) -> dict[str, str]:
    settings = default_mode_settings(allowed_symbols)
    for key, value in settings.items():
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
    conn.commit()
    return settings


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


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
    if raw > 1e17:  # ns
        raw /= 1_000_000_000
    elif raw > 1e14:  # us
        raw /= 1_000_000
    elif raw > 1e11:  # ms
        raw /= 1_000
    return datetime.fromtimestamp(raw, tz=timezone.utc).replace(microsecond=0).isoformat()


def polygon_symbol_supported(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}", symbol.upper()))


def merge_quotes(primary: pd.DataFrame, secondary: pd.DataFrame, symbols: tuple[str, ...]) -> pd.DataFrame:
    parts = []
    if primary is not None and not primary.empty:
        parts.append(primary.copy())
    if secondary is not None and not secondary.empty:
        parts.append(secondary.copy())
    if not parts:
        return pd.DataFrame(
            columns=[
                "symbol",
                "last",
                "previous",
                "change_pct",
                "quote_time_utc",
                "market_state",
                "currency",
                "source",
                "regular_price",
                "pre_price",
                "post_price",
                "official_close",
                "price_context",
            ]
        )
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["symbol"], keep="first")
    order = {s: i for i, s in enumerate(symbols)}
    merged["order"] = merged["symbol"].map(order).fillna(10_000)
    return merged.sort_values(["order", "symbol"]).drop(columns=["order"]).reset_index(drop=True)


class PolygonTickStream:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._api_key = ""
        self._symbols: tuple[str, ...] = ()
        self._quotes: dict[str, dict] = {}
        self._status = "stopped"
        self._last_error = ""
        self._last_message_utc: str | None = None
        self._last_tick_utc: str | None = None

    def configure(self, api_key: str, symbols: list[str]) -> None:
        clean_symbols = tuple(sorted(set([s.upper() for s in symbols if polygon_symbol_supported(s)])))
        api_key = api_key.strip()
        with self._lock:
            current_key = self._api_key
            current_symbols = self._symbols
            running = self._thread is not None and self._thread.is_alive()

        if running and current_key == api_key and current_symbols == clean_symbols:
            return

        self.stop()
        if not api_key or not clean_symbols:
            with self._lock:
                self._api_key = api_key
                self._symbols = clean_symbols
                self._status = "idle"
            return

        with self._lock:
            self._api_key = api_key
            self._symbols = clean_symbols
            self._stop_event.clear()
            self._status = "starting"
            self._last_error = ""

        thread = threading.Thread(target=self._thread_main, name="polygon-tick-stream", daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=2)
        with self._lock:
            self._thread = None
            if self._status != "idle":
                self._status = "stopped"

    def status(self) -> dict[str, str | int | None]:
        with self._lock:
            return {
                "status": self._status,
                "symbols": len(self._symbols),
                "last_error": self._last_error,
                "last_message_utc": self._last_message_utc,
                "last_tick_utc": self._last_tick_utc,
            }

    def quotes_df(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._quotes.values())
        if not rows:
            return pd.DataFrame(
                columns=["symbol", "last", "previous", "change_pct", "quote_time_utc", "market_state", "currency", "source"]
            )
        return pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)

    def _thread_main(self) -> None:
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        try:
            import websockets
        except Exception as exc:
            with self._lock:
                self._status = "error"
                self._last_error = f"websockets manquant: {exc}"
            return

        while not self._stop_event.is_set():
            with self._lock:
                api_key = self._api_key
                symbols = self._symbols
            if not api_key or not symbols:
                with self._lock:
                    self._status = "idle"
                await asyncio.sleep(1)
                continue

            subscribe = ",".join([f"T.{s}" for s in symbols])
            try:
                async with websockets.connect("wss://socket.polygon.io/stocks", ping_interval=15, ping_timeout=15) as ws:
                    with self._lock:
                        self._status = "authenticating"
                        self._last_error = ""
                    await ws.send(json.dumps({"action": "auth", "params": api_key}))
                    await ws.send(json.dumps({"action": "subscribe", "params": subscribe}))
                    with self._lock:
                        self._status = "connected"

                    while not self._stop_event.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        payload = json.loads(raw)
                        msgs = payload if isinstance(payload, list) else [payload]
                        self._handle_messages(msgs)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:  # pragma: no cover - runtime network
                with self._lock:
                    self._status = "reconnecting"
                    self._last_error = str(exc)
                await asyncio.sleep(1.5)

        with self._lock:
            self._status = "stopped"

    def _handle_messages(self, messages: list[dict]) -> None:
        now_iso = utc_now_iso()
        with self._lock:
            for msg in messages:
                event = msg.get("ev")
                if event == "status":
                    self._last_message_utc = now_iso
                    status = str(msg.get("status", "")).lower()
                    if "auth" in status and "fail" in status:
                        self._status = "error"
                        self._last_error = str(msg.get("message", "auth failed"))
                    continue
                if event != "T":
                    continue
                symbol = str(msg.get("sym", "")).upper()
                if not symbol:
                    continue
                price = msg.get("p")
                if price is None:
                    continue
                previous = self._quotes.get(symbol, {}).get("last", float(price))
                change_pct = ((float(price) / float(previous)) - 1) * 100 if float(previous) else 0.0
                self._quotes[symbol] = {
                    "symbol": symbol,
                    "last": float(price),
                    "previous": float(previous),
                    "change_pct": float(change_pct),
                    "quote_time_utc": any_epoch_to_iso(msg.get("t")),
                    "market_state": "STREAMING",
                    "currency": "USD",
                    "source": "polygon_ws_tick",
                    "regular_price": float(price),
                    "pre_price": 0.0,
                    "post_price": 0.0,
                    "official_close": float(previous),
                    "price_context": "tick",
                }
                self._last_message_utc = now_iso
                self._last_tick_utc = now_iso

    def is_stale(self, stale_after_seconds: int) -> bool:
        with self._lock:
            last_tick = self._last_tick_utc
            status = self._status
        if status not in {"connected", "reconnecting", "authenticating"}:
            return False
        if not last_tick:
            return True
        ts = pd.Timestamp(last_tick)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        age = (pd.Timestamp.now(tz="UTC") - ts).total_seconds()
        return age >= max(stale_after_seconds, 1)


@st.cache_resource
def get_polygon_tick_stream() -> PolygonTickStream:
    return PolygonTickStream()


@st.cache_resource
def get_connection(db_path_str: str) -> sqlite3.Connection:
    db_path = Path(db_path_str)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn

def ensure_column_exists(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            executed_at_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            fees REAL NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'EUR',
            fx_to_base REAL DEFAULT 1.0,
            strategy_tag TEXT,
            exchange TEXT NOT NULL,
            note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at_utc TEXT NOT NULL,
            portfolio_value REAL NOT NULL,
            cash REAL NOT NULL,
            invested REAL NOT NULL,
            pnl REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            event_type TEXT,
            event_label TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at_utc TEXT NOT NULL,
            alert_key TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT,
            delivered INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at_utc TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbols_csv TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            metrics_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at_utc TEXT NOT NULL,
            level TEXT NOT NULL,
            event TEXT NOT NULL,
            details_json TEXT
        )
        """
    )

    ensure_column_exists(conn, "transactions", "currency", "TEXT DEFAULT 'EUR'")
    ensure_column_exists(conn, "transactions", "fx_to_base", "REAL DEFAULT 1.0")
    ensure_column_exists(conn, "transactions", "strategy_tag", "TEXT")
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('initial_capital', ?)",
        (str(DEFAULT_INITIAL_CAPITAL),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('exchange', ?)",
        (DEFAULT_EXCHANGE,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('live_enabled', ?)",
        ("1",),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('refresh_seconds', ?)",
        (str(DEFAULT_REFRESH_SECONDS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('realtime_symbols', ?)",
        (",".join(DEFAULT_REALTIME_SYMBOLS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('live_mode', ?)",
        (DEFAULT_LIVE_MODE,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('base_currency', ?)",
        (DEFAULT_BASE_CURRENCY,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('accounting_method', ?)",
        (DEFAULT_ACCOUNTING_METHOD,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('snapshot_min_seconds', ?)",
        (str(DEFAULT_SNAPSHOT_MIN_SECONDS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('snapshot_min_delta', ?)",
        (str(DEFAULT_SNAPSHOT_MIN_DELTA),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('ws_stale_seconds', ?)",
        (str(DEFAULT_WS_STALE_SECONDS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('max_line_pct', ?)",
        (str(DEFAULT_MAX_LINE_PCT),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('max_sector_pct', ?)",
        (str(DEFAULT_MAX_SECTOR_PCT),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('max_zone_pct', ?)",
        (str(DEFAULT_MAX_ZONE_PCT),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('alert_loss_pct', ?)",
        (str(DEFAULT_ALERT_LOSS_PCT),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('alert_drawdown_pct', ?)",
        (str(DEFAULT_ALERT_DRAWDOWN_PCT),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('alert_gain_pct', ?)",
        (str(DEFAULT_ALERT_GAIN_PCT),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('alert_webhook_url', '')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('alert_email_to', '')"
    )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def get_setting_float(conn: sqlite3.Connection, key: str, default: float) -> float:
    return safe_float(get_setting(conn, key, str(default)), default)


def get_setting_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    raw = get_setting(conn, key, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def log_event(conn: sqlite3.Connection, level: str, event: str, details: dict | None = None) -> None:
    details_json = json.dumps(details or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO app_logs(created_at_utc, level, event, details_json)
        VALUES (?, ?, ?, ?)
        """,
        (utc_now_iso(), level.upper(), event, details_json),
    )
    conn.commit()
    if level.upper() == "ERROR":
        LOGGER.error("%s %s", event, details_json)
    elif level.upper() == "WARNING":
        LOGGER.warning("%s %s", event, details_json)
    else:
        LOGGER.info("%s %s", event, details_json)


def load_recent_logs(conn: sqlite3.Connection, limit: int = 100) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT created_at_utc, level, event, details_json
        FROM app_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(int(limit),),
        parse_dates=["created_at_utc"],
    )


def load_transactions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT id, executed_at_utc, symbol, side, quantity, price, fees, currency, fx_to_base, strategy_tag, exchange, note
        FROM transactions
        ORDER BY executed_at_utc ASC, id ASC
        """,
        conn,
        parse_dates=["executed_at_utc"],
    )
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "executed_at_utc",
                "symbol",
                "side",
                "quantity",
                "price",
                "fees",
                "currency",
                "fx_to_base",
                "strategy_tag",
                "exchange",
                "note",
            ]
        )
    df["symbol"] = df["symbol"].str.upper()
    df["currency"] = df["currency"].fillna(DEFAULT_BASE_CURRENCY).str.upper()
    df["fx_to_base"] = pd.to_numeric(df["fx_to_base"], errors="coerce").fillna(1.0)
    return df


def load_snapshots(conn: sqlite3.Connection) -> pd.DataFrame:
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


def insert_transaction(
    conn: sqlite3.Connection,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    fees: float,
    currency: str,
    fx_to_base: float,
    exchange: str,
    strategy_tag: str | None,
    note: str,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions(executed_at_utc, symbol, side, quantity, price, fees, currency, fx_to_base, strategy_tag, exchange, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            symbol.upper(),
            side.upper(),
            quantity,
            price,
            fees,
            currency.upper() if currency else DEFAULT_BASE_CURRENCY,
            fx_to_base if fx_to_base > 0 else 1.0,
            strategy_tag.strip() if strategy_tag else None,
            exchange,
            note.strip() or None,
        ),
    )
    conn.commit()


def upsert_snapshot(
    conn: sqlite3.Connection,
    snapshot: dict[str, float],
    explicit_event: str | None = None,
    explicit_label: str | None = None,
    min_delta_eur: float = DEFAULT_SNAPSHOT_MIN_DELTA,
    min_seconds: int = DEFAULT_SNAPSHOT_MIN_SECONDS,
) -> None:
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
        cash_delta = float(snapshot["cash"] - last["cash"])
        invested_delta = float(snapshot["invested"] - last["invested"])
        last_ts = pd.Timestamp(last["captured_at_utc"])
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        elapsed = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds()

        if explicit_event is None:
            if elapsed < max(min_seconds, 1):
                return
            if abs(value_delta) < max(min_delta_eur, 0.01) and abs(cash_delta) < 0.01 and abs(invested_delta) < 0.01:
                return
            event_type = "UP" if value_delta > 0 else "DOWN"
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


def compute_cash(initial_capital: float, transactions: pd.DataFrame) -> float:
    if transactions.empty:
        return initial_capital
    cash = float(initial_capital)
    for tx in transactions.itertuples(index=False):
        notional = float(tx.quantity) * float(tx.price)
        fees = float(tx.fees)
        fx_to_base = safe_float(getattr(tx, "fx_to_base", 1.0), 1.0)
        signed_amount = (notional + fees) if str(tx.side).upper() == "BUY" else -(notional - fees)
        base_amount = signed_amount * (fx_to_base if fx_to_base > 0 else 1.0)
        if str(tx.side).upper() == "BUY":
            cash -= base_amount
        else:
            cash -= base_amount
    return cash


def compute_positions(transactions: pd.DataFrame, accounting_method: str = DEFAULT_ACCOUNTING_METHOD) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "quantity",
                "avg_cost",
                "book_value",
                "realized_pnl",
                "realized_pnl_base",
                "currency",
                "avg_fx_to_base",
            ]
        )

    method = accounting_method.strip().lower() if accounting_method else DEFAULT_ACCOUNTING_METHOD
    if method not in {"fifo", "lifo", "average"}:
        method = DEFAULT_ACCOUNTING_METHOD

    ledgers: dict[str, dict] = {}
    for tx in transactions.itertuples(index=False):
        symbol = str(tx.symbol).upper()
        side = str(tx.side).upper()
        qty = safe_float(tx.quantity, 0.0)
        price = safe_float(tx.price, 0.0)
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
                "fx_to_base_values": [],
            },
        )
        ledger["currency"] = currency
        if fx_to_base > 0:
            ledger["fx_to_base_values"].append(fx_to_base)

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
        lot_index = 0
        while remaining > 1e-12 and ledger["lots"]:
            if method == "lifo":
                lot = ledger["lots"][-1]
                lot_index = len(ledger["lots"]) - 1
            else:
                lot = ledger["lots"][0]
                lot_index = 0
            matched = min(remaining, lot["qty"])
            cost_basis += matched * lot["unit_cost"]
            lot_fx = safe_float(lot.get("fx_to_base", fx_to_base), fx_to_base if fx_to_base > 0 else 1.0)
            cost_basis_base += matched * lot["unit_cost"] * lot_fx
            lot["qty"] -= matched
            remaining -= matched
            if lot["qty"] <= 1e-12:
                ledger["lots"].pop(lot_index)

        realized_quote = proceeds_net - cost_basis
        realized_base = proceeds_net * (fx_to_base if fx_to_base > 0 else 1.0) - cost_basis_base
        ledger["realized_pnl"] += realized_quote
        ledger["realized_pnl_base"] += realized_base
        ledger["quantity"] -= sell_qty
        if ledger["quantity"] <= 1e-12:
            ledger["quantity"] = 0.0
            ledger["lots"] = []

    rows = []
    for ledger in ledgers.values():
        lots = ledger["lots"]
        qty = sum([lot["qty"] for lot in lots])
        cost_sum = sum([lot["qty"] * lot["unit_cost"] for lot in lots])
        avg_cost = cost_sum / qty if qty > 0 else 0.0
        avg_fx = np.mean(ledger["fx_to_base_values"]) if ledger["fx_to_base_values"] else 1.0
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
        return pd.DataFrame(
            columns=[
                "symbol",
                "quantity",
                "avg_cost",
                "book_value",
                "realized_pnl",
                "realized_pnl_base",
                "currency",
                "avg_fx_to_base",
            ]
        )
    return df.sort_values("symbol").reset_index(drop=True)


def _safe_yf_import():
    try:
        import yfinance as yf

        return yf
    except Exception:
        return None


@st.cache_data(ttl=120, show_spinner=False)
def fetch_quotes(symbols: tuple[str, ...]) -> pd.DataFrame:
    yf = _safe_yf_import()
    if not yf or not symbols:
        return pd.DataFrame(columns=["symbol", "last", "previous", "change_pct"])

    rows = []
    for symbol in symbols:
        try:
            hist = yf.Ticker(symbol).history(period="10d", interval="1d", auto_adjust=False)
            close = hist["Close"].dropna()
            if close.empty:
                continue
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else last
            change_pct = ((last / prev) - 1) * 100 if prev else 0.0
            rows.append({"symbol": symbol, "last": last, "previous": prev, "change_pct": change_pct})
        except Exception:
            continue
    return pd.DataFrame(rows)


@st.cache_data(ttl=5, show_spinner=False)
def fetch_realtime_quotes(symbols: tuple[str, ...]) -> pd.DataFrame:
    columns = [
        "symbol",
        "last",
        "previous",
        "change_pct",
        "quote_time_utc",
        "market_state",
        "currency",
        "source",
        "regular_price",
        "pre_price",
        "post_price",
        "official_close",
        "price_context",
    ]
    cleaned = tuple(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()]))
    if not cleaned:
        return pd.DataFrame(columns=columns)

    rows_by_symbol: dict[str, dict] = {}
    for part in chunked(list(cleaned), 50):
        try:
            joined = ",".join(part)
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={urllib.parse.quote(joined)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            quotes = payload.get("quoteResponse", {}).get("result", [])
        except Exception:
            quotes = []

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
            if market_state.upper().startswith("PRE") and pre_price is not None:
                last = pre_price
                price_context = "pre"
            elif market_state.upper().startswith("POST") and post_price is not None:
                last = post_price
                price_context = "post"
            elif last is None:
                last = post_price if post_price is not None else pre_price
                price_context = "off_session"
            if last is None:
                last = q.get("bid")
                price_context = "bid_fallback"
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
            }

    missing = [s for s in cleaned if s not in rows_by_symbol]
    if missing:
        fallback = fetch_quotes(tuple(missing))
        if not fallback.empty:
            for row in fallback.itertuples(index=False):
                rows_by_symbol[str(row.symbol).upper()] = {
                    "symbol": str(row.symbol).upper(),
                    "last": float(row.last),
                    "previous": float(row.previous),
                    "change_pct": float(row.change_pct),
                    "quote_time_utc": utc_now_iso(),
                    "market_state": "DELAYED",
                    "currency": "",
                    "source": "yfinance_history",
                    "regular_price": float(row.last),
                    "pre_price": 0.0,
                    "post_price": 0.0,
                    "official_close": float(row.previous),
                    "price_context": "delayed",
                }

    if not rows_by_symbol:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows_by_symbol.values()).sort_values("symbol").reset_index(drop=True)


def infer_currency(symbol: str, quote_currency: str | None, base_currency: str) -> str:
    if quote_currency:
        c = quote_currency.strip().upper()
        if c:
            return c
    s = symbol.upper()
    if s.endswith(".PA") or s.endswith(".AS"):
        return "EUR"
    if s.endswith(".T"):
        return "JPY"
    if s.endswith(".HK"):
        return "HKD"
    return "USD" if base_currency != "USD" else base_currency


@st.cache_data(ttl=60, show_spinner=False)
def fetch_fx_rates(base_currency: str, currencies: tuple[str, ...]) -> dict[str, float]:
    base = base_currency.upper()
    rates: dict[str, float] = {base: 1.0}
    needed = sorted(set([c.upper() for c in currencies if c and c.upper() != base]))
    if not needed:
        return rates

    symbols = [f"{c}{base}=X" for c in needed]
    quotes = fetch_realtime_quotes(tuple(symbols))
    if not quotes.empty:
        for row in quotes.itertuples(index=False):
            sym = str(row.symbol).upper()
            if sym.endswith("=X") and len(sym) >= 7:
                cur = sym[:3]
                rates[cur] = safe_float(row.last, 0.0)

    unresolved = [c for c in needed if c not in rates or rates[c] <= 0]
    if unresolved:
        reverse_symbols = [f"{base}{c}=X" for c in unresolved]
        rev_quotes = fetch_realtime_quotes(tuple(reverse_symbols))
        if not rev_quotes.empty:
            for row in rev_quotes.itertuples(index=False):
                sym = str(row.symbol).upper()
                if sym.endswith("=X") and len(sym) >= 7:
                    cur = sym[3:6]
                    val = safe_float(row.last, 0.0)
                    if val > 0:
                        rates[cur] = 1.0 / val

    for c in needed:
        if c not in rates or rates[c] <= 0:
            rates[c] = 1.0 if c == base else np.nan
    return rates


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_profiles(symbols: tuple[str, ...]) -> dict[str, dict]:
    yf = _safe_yf_import()
    result: dict[str, dict] = {}
    for symbol in symbols:
        base = CATALOG_BY_SYMBOL.get(symbol, {})
        profile = {
            "name": base.get("name", symbol),
            "sector": base.get("sector", "Non classé"),
            "country": None,
            "zone": base.get("zone", "USA"),
            "asset_type": base.get("asset_type", "Action"),
            "dividend_yield": 0.0,
        }
        if yf:
            try:
                info = yf.Ticker(symbol).info
                profile["name"] = info.get("shortName") or info.get("longName") or profile["name"]
                profile["sector"] = info.get("sector") or profile["sector"]
                profile["country"] = info.get("country") or profile["country"]
                profile["asset_type"] = info.get("quoteType") or profile["asset_type"]
                profile["dividend_yield"] = float(info.get("dividendYield") or 0.0)
            except Exception:
                pass
        if profile["country"]:
            profile["zone"] = COUNTRY_TO_ZONE.get(profile["country"], profile["zone"])
        result[symbol] = profile
    return result


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_geopolitical_news(max_items: int = 8) -> list[dict[str, str]]:
    query = urllib.parse.quote("géopolitique marchés financiers sanctions pétrole asie europe usa")
    url = f"https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = response.read()
        root = ET.fromstring(payload)
    except Exception:
        return []

    items: list[dict[str, str]] = []
    for item in root.findall(".//item")[:max_items]:
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
            }
        )
    return items


@st.cache_data(ttl=900, show_spinner=False)
def fetch_signal_metrics(symbols: tuple[str, ...]) -> pd.DataFrame:
    yf = _safe_yf_import()
    if not yf or not symbols:
        return pd.DataFrame(columns=["symbol", "ret_1m", "ret_3m", "vol_3m"])

    rows = []
    for symbol in symbols:
        try:
            hist = yf.Ticker(symbol).history(period="7mo", interval="1d", auto_adjust=False)
            close = hist["Close"].dropna()
            if len(close) < 30:
                continue
            r1 = float(close.iloc[-1] / close.iloc[-22] - 1) if len(close) > 22 else np.nan
            r3 = float(close.iloc[-1] / close.iloc[-66] - 1) if len(close) > 66 else np.nan
            vol = float(close.pct_change().tail(63).std(ddof=1) * np.sqrt(252))
            rows.append({"symbol": symbol, "ret_1m": r1, "ret_3m": r3, "vol_3m": vol})
        except Exception:
            continue
    return pd.DataFrame(rows)


def compute_portfolio_state(
    initial_capital: float,
    transactions: pd.DataFrame,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    profiles: dict[str, dict],
    base_currency: str,
    fx_rates: dict[str, float],
) -> tuple[pd.DataFrame, dict[str, float]]:
    quote_map = quotes.set_index("symbol").to_dict(orient="index") if not quotes.empty else {}
    holdings_rows = []
    for pos in positions.itertuples(index=False):
        q = quote_map.get(pos.symbol, {})
        quote_currency = infer_currency(pos.symbol, str(q.get("currency", "")), base_currency)
        fx_to_base = safe_float(fx_rates.get(quote_currency, np.nan), np.nan)
        if np.isnan(fx_to_base) or fx_to_base <= 0:
            fx_to_base = safe_float(getattr(pos, "avg_fx_to_base", 1.0), 1.0)
        avg_fx_to_base = safe_float(getattr(pos, "avg_fx_to_base", fx_to_base), fx_to_base)
        last = float(q.get("last", pos.avg_cost))
        market_value_quote = float(pos.quantity * last)
        market_value = float(market_value_quote * fx_to_base)
        book_value_base = float(pos.quantity * pos.avg_cost * avg_fx_to_base)
        unrealized = market_value - book_value_base
        realized_base = safe_float(getattr(pos, "realized_pnl_base", np.nan), np.nan)
        if np.isnan(realized_base):
            realized_base = safe_float(getattr(pos, "realized_pnl", 0.0), 0.0) * fx_to_base
        profile = profiles.get(pos.symbol, {})
        holdings_rows.append(
            {
                "symbol": pos.symbol,
                "nom": profile.get("name", pos.symbol),
                "zone": profile.get("zone", CATALOG_BY_SYMBOL.get(pos.symbol, {}).get("zone", "USA")),
                "secteur": profile.get("sector", CATALOG_BY_SYMBOL.get(pos.symbol, {}).get("sector", "Non classé")),
                "type": CATALOG_BY_SYMBOL.get(pos.symbol, {}).get("asset_type", profile.get("asset_type", "Action")),
                "quantite": float(pos.quantity),
                "prix_moyen": float(pos.avg_cost),
                "cours": last,
                "devise": quote_currency,
                "fx_to_base": fx_to_base,
                "valeur_marche": market_value,
                "valeur_marche_devise": market_value_quote,
                "pnl_latent": unrealized,
                "pnl_realise": float(realized_base),
                "dividend_yield": float(profile.get("dividend_yield", 0.0)),
            }
        )

    holdings = pd.DataFrame(holdings_rows)
    if holdings.empty:
        invested = 0.0
        annual_dividends = 0.0
    else:
        invested = float(holdings["valeur_marche"].sum())
        annual_dividends = float((holdings["valeur_marche"] * holdings["dividend_yield"]).sum())

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


def compute_geopolitical_risk(news: list[dict[str, str]]) -> tuple[str, int]:
    score = 0
    for item in news:
        title = item.get("title", "").lower()
        for keyword, weight in RISK_KEYWORDS.items():
            if keyword in title:
                score += weight
    if score >= 18:
        return "Élevé", score
    if score >= 8:
        return "Modéré", score
    return "Faible", score


def local_ai_assistant(
    objective: str,
    question: str,
    profile: str,
    horizon_years: int,
    state: dict[str, float],
    opportunities: pd.DataFrame,
    vigilance: pd.DataFrame,
    geo_risk_level: str,
) -> str:
    risk_budget = {"Prudent": "faible", "Équilibré": "moyen", "Dynamique": "élevé"}.get(profile, "moyen")
    base_currency = str(state.get("base_currency", DEFAULT_BASE_CURRENCY))
    actions: list[str] = []
    actions.append(
        f"Contexte portefeuille: capital {money(state['portfolio_value'], base_currency)}, performance {money(state['pnl'], base_currency)} ({pct(state['pnl_pct'])})."
    )
    actions.append(f"Profil déclaré: {profile} ({risk_budget} budget risque), horizon: {horizon_years} ans.")
    actions.append(f"Niveau de risque géopolitique observé: {geo_risk_level}.")
    if not opportunities.empty:
        top = opportunities.iloc[0]
        actions.append(
            f"Opportunité technique courte liste: {top['symbol']} (1 mois {top['ret_1m'] * 100:+.1f}%, vol 3 mois {top['vol_3m'] * 100:.1f}%)."
        )
    if not vigilance.empty:
        hot = vigilance.iloc[0]
        actions.append(
            f"Point de vigilance prioritaire: {hot['symbol']} (1 mois {hot['ret_1m'] * 100:+.1f}%, vol 3 mois {hot['vol_3m'] * 100:.1f}%)."
        )
    actions.append("Cadre d'exécution recommandé:")
    actions.append("1) Définir un poids maximal par ligne (5-10% prudent, 10-15% équilibré, 15-20% dynamique).")
    actions.append("2) Exécuter par paliers et journaliser chaque achat/vente pour conserver la cohérence des snapshots.")
    actions.append("3) Mettre en place un seuil de réduction des risques si la valeur totale baisse de 5% à 8% depuis un sommet récent.")
    if objective.strip():
        actions.append(f"Objectif utilisateur: {objective.strip()}")
    if question.strip():
        actions.append(f"Réponse ciblée à la question: {question.strip()} -> privilégier des décisions progressives plutôt qu'un all-in.")
    actions.append("Ce module est une aide quantitative et ne remplace pas un conseil financier personnalisé.")
    return "\n".join(actions)


def openai_ai_assistant(prompt: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": "Tu es un analyste portefeuille. Réponse concise, actionnable, avec gestion du risque."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_output_tokens=500,
        )
        return response.output_text.strip()
    except Exception:
        return None


def render_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --brand-blue:#0f2f79;
            --brand-green:#16a34a;
            --soft-bg:#f2f5fb;
            --card-radius:20px;
        }
        .stApp {
            background:
                radial-gradient(circle at 10% 10%, rgba(26, 73, 165, 0.06), transparent 32%),
                radial-gradient(circle at 80% 15%, rgba(22, 163, 74, 0.05), transparent 30%),
                linear-gradient(180deg, #f7f9fc 0%, #eef3fb 100%);
        }
        .main-title {
            color: #102a5c;
            font-size: 2rem;
            font-weight: 800;
            margin-bottom: 0.1rem;
        }
        .subtitle {
            color:#4b5563;
            margin-bottom: 1rem;
        }
        .metric-card {
            border: 2px solid #d7deea;
            border-radius: var(--card-radius);
            padding: 1rem 1.1rem;
            background: white;
            box-shadow: 0 8px 24px rgba(20, 42, 90, 0.08);
            min-height: 170px;
        }
        .metric-card.primary {
            background: linear-gradient(160deg, #0f2f79 0%, #1a3f95 100%);
            color: white;
            border-color: #0f2f79;
        }
        .metric-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin-bottom: 0.7rem;
        }
        .metric-value {
            font-size: 2.1rem;
            font-weight: 800;
            margin-bottom: 0.35rem;
            line-height: 1.05;
        }
        .metric-sub {
            color: #64748b;
            font-size: 1rem;
            margin-bottom: 0.4rem;
        }
        .metric-card.primary .metric-sub {
            color: rgba(255,255,255,0.88);
        }
        .event-pill {
            display:inline-block;
            padding: 0.2rem 0.5rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 700;
            background: #dcfce7;
            color: #166534;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric_card(
    title: str,
    value: str,
    subtitle: str,
    primary: bool = False,
    badge: str | None = None,
) -> None:
    card_class = "metric-card primary" if primary else "metric-card"
    badge_html = f"<div class='event-pill'>{badge}</div>" if badge else ""
    st.markdown(
        f"""
        <div class="{card_class}">
            <div class="metric-title">{title}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-sub">{subtitle}</div>
            {badge_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def create_evolution_chart(snapshots: pd.DataFrame, currency: str = "EUR") -> go.Figure:
    fig = go.Figure()
    if snapshots.empty:
        fig.update_layout(title="Aucun snapshot disponible")
        return fig
    points = snapshots.copy()
    points["captured_local"] = pd.to_datetime(points["captured_at_utc"], utc=True).dt.tz_convert(DISPLAY_TZ)
    fig.add_trace(
        go.Scatter(
            x=points["captured_local"],
            y=points["portfolio_value"],
            mode="lines",
            line={"color": "#1d4ed8", "width": 3},
            name="Capital total",
        )
    )
    marks = points[points["event_type"].notna()]
    if not marks.empty:
        fig.add_trace(
            go.Scatter(
                x=marks["captured_local"],
                y=marks["portfolio_value"],
                mode="markers",
                marker={
                    "size": 9,
                    "color": [EVENT_COLORS.get(v, "#6b7280") for v in marks["event_type"]],
                    "line": {"color": "white", "width": 1},
                },
                text=marks["event_label"].fillna(marks["event_type"]),
                hovertemplate=f"%{{x|%d/%m/%Y %H:%M}}<br>%{{y:.2f}} {currency}<br>%{{text}}<extra></extra>",
                name="Événements",
            )
        )
    fig.update_layout(
        title="Évolution du portefeuille (snapshots achats/ventes/hausses/baisses)",
        yaxis_title=f"Valeur ({currency})",
        xaxis_title="Date",
        template="plotly_white",
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
    )
    return fig


def create_allocation_chart(data: pd.DataFrame, label_col: str, value_col: str, title: str) -> go.Figure:
    if data.empty or float(data[value_col].sum()) <= 0:
        fig = go.Figure()
        fig.update_layout(title=f"{title} (vide)")
        return fig
    fig = px.pie(
        data,
        names=label_col,
        values=value_col,
        hole=0.55,
        title=title,
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(margin={"l": 20, "r": 20, "t": 55, "b": 20})
    return fig


def create_market_clock_card(exchange: str) -> tuple[str, str, str]:
    try:
        clock = get_market_clock(exchange=exchange)
        status = "Ouvert" if clock.is_open else "Fermé"
        subtitle = f"Prochaine ouverture: {to_display_time(clock.next_open_utc)}"
        detail = f"Prochaine clôture: {to_display_time(clock.next_close_utc)}"
        return status, subtitle, detail
    except Exception as exc:
        return "Indisponible", f"Horloge marché non disponible ({exchange})", str(exc)


def opportunities_and_vigilance(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if metrics.empty:
        return metrics, metrics
    clean = metrics.dropna(subset=["ret_1m", "vol_3m"]).copy()
    opportunities = clean[(clean["ret_1m"] > 0.04) & (clean["vol_3m"] < 0.35)].sort_values("ret_1m", ascending=False).head(8)
    vigilance = clean[(clean["ret_1m"] < -0.04) | (clean["vol_3m"] > 0.45)].sort_values(
        ["ret_1m", "vol_3m"], ascending=[True, False]
    ).head(8)
    return opportunities, vigilance


def compute_drawdown_pct(snapshots: pd.DataFrame) -> float:
    if snapshots.empty:
        return 0.0
    values = pd.to_numeric(snapshots["portfolio_value"], errors="coerce").dropna()
    if values.empty:
        return 0.0
    peak = values.cummax()
    dd = values / peak - 1.0
    return float(dd.iloc[-1] * 100)


def check_trade_risk(
    side: str,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    cash: float,
    holdings: pd.DataFrame,
    base_currency: str,
    fx_to_base: float,
    max_line_pct: float,
    max_sector_pct: float,
    max_zone_pct: float,
) -> list[str]:
    errors: list[str] = []
    buy_cost_base = (quantity * price + fees) * fx_to_base
    sell_proceeds_base = max(quantity * price - fees, 0.0) * fx_to_base
    current_value = float(holdings["valeur_marche"].sum()) if not holdings.empty else 0.0
    total_after = cash + current_value
    if side.upper() == "BUY":
        if cash < buy_cost_base:
            errors.append(f"Cash insuffisant: requis {money(buy_cost_base, base_currency)}, disponible {money(cash, base_currency)}.")
        projected_total = total_after
        symbol_value = float(holdings.loc[holdings["symbol"] == symbol, "valeur_marche"].sum()) + buy_cost_base
    else:
        projected_total = total_after
        symbol_value = max(0.0, float(holdings.loc[holdings["symbol"] == symbol, "valeur_marche"].sum()) - sell_proceeds_base)

    if projected_total <= 0:
        return errors

    line_pct = (symbol_value / projected_total) * 100
    if line_pct > max_line_pct:
        errors.append(f"Limite ligne dépassée ({line_pct:.1f}% > {max_line_pct:.1f}%).")

    if not holdings.empty:
        tmp = holdings.copy()
        if symbol in tmp["symbol"].values:
            idx = tmp.index[tmp["symbol"] == symbol][0]
            current = float(tmp.loc[idx, "valeur_marche"])
            tmp.loc[idx, "valeur_marche"] = current + (buy_cost_base if side.upper() == "BUY" else -sell_proceeds_base)
            tmp.loc[idx, "valeur_marche"] = max(float(tmp.loc[idx, "valeur_marche"]), 0.0)
        else:
            meta = CATALOG_BY_SYMBOL.get(symbol, {})
            tmp = pd.concat(
                [
                    tmp,
                    pd.DataFrame(
                        [
                            {
                                "symbol": symbol,
                                "zone": meta.get("zone", "USA"),
                                "secteur": meta.get("sector", "Non classé"),
                                "valeur_marche": buy_cost_base if side.upper() == "BUY" else 0.0,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
    else:
        meta = CATALOG_BY_SYMBOL.get(symbol, {})
        tmp = pd.DataFrame(
            [{"symbol": symbol, "zone": meta.get("zone", "USA"), "secteur": meta.get("sector", "Non classé"), "valeur_marche": buy_cost_base}]
        )
    tmp = tmp[tmp["valeur_marche"] > 0]
    if not tmp.empty:
        sector_max = float(tmp.groupby("secteur")["valeur_marche"].sum().max() / projected_total * 100)
        zone_max = float(tmp.groupby("zone")["valeur_marche"].sum().max() / projected_total * 100)
        if sector_max > max_sector_pct:
            errors.append(f"Limite sectorielle dépassée ({sector_max:.1f}% > {max_sector_pct:.1f}%).")
        if zone_max > max_zone_pct:
            errors.append(f"Limite géographique dépassée ({zone_max:.1f}% > {max_zone_pct:.1f}%).")
    return errors


def insert_alert(conn: sqlite3.Connection, alert_key: str, severity: str, title: str, message: str, payload: dict | None = None) -> bool:
    last = conn.execute(
        """
        SELECT created_at_utc
        FROM alert_events
        WHERE alert_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (alert_key,),
    ).fetchone()
    if last:
        prev_ts = pd.Timestamp(last["created_at_utc"])
        if prev_ts.tzinfo is None:
            prev_ts = prev_ts.tz_localize("UTC")
        age = (pd.Timestamp.now(tz="UTC") - prev_ts).total_seconds()
        if age < ALERT_COOLDOWN_SECONDS:
            return False
    conn.execute(
        """
        INSERT INTO alert_events(created_at_utc, alert_key, severity, title, message, payload_json, delivered)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (utc_now_iso(), alert_key, severity, title, message, json.dumps(payload or {}, ensure_ascii=False)),
    )
    conn.commit()
    return True


def load_recent_alerts(conn: sqlite3.Connection, limit: int = 25) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT id, created_at_utc, severity, title, message, payload_json, delivered
        FROM alert_events
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(int(limit),),
        parse_dates=["created_at_utc"],
    )


def send_webhook_alert(webhook_url: str, payload: dict) -> bool:
    if not webhook_url.strip():
        return False
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook_url.strip(), data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=6):
            return True
    except Exception:
        return False


def send_email_alert(to_email: str, subject: str, body: str) -> bool:
    to_email = to_email.strip()
    if not to_email:
        return False
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    pwd = os.getenv("SMTP_PASS", "").strip()
    from_email = os.getenv("SMTP_FROM", user or "portfolio@localhost")
    if not host or not user or not pwd:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=8) as smtp:
            smtp.starttls()
            smtp.login(user, pwd)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def deliver_pending_alerts(conn: sqlite3.Connection, webhook_url: str, email_to: str) -> int:
    pending = pd.read_sql_query(
        """
        SELECT id, created_at_utc, severity, title, message, payload_json
        FROM alert_events
        WHERE delivered = 0
        ORDER BY id ASC
        LIMIT 20
        """,
        conn,
    )
    delivered = 0
    for row in pending.itertuples(index=False):
        payload = {
            "id": int(row.id),
            "created_at_utc": str(row.created_at_utc),
            "severity": str(row.severity),
            "title": str(row.title),
            "message": str(row.message),
            "payload": json.loads(row.payload_json or "{}"),
        }
        ok_webhook = send_webhook_alert(webhook_url, payload)
        ok_email = send_email_alert(email_to, f"[Portfolio Alert] {row.title}", f"{row.message}\n\n{json.dumps(payload['payload'], ensure_ascii=False, indent=2)}")
        if ok_webhook or ok_email:
            conn.execute("UPDATE alert_events SET delivered = 1 WHERE id = ?", (int(row.id),))
            conn.commit()
            delivered += 1
    return delivered


def evaluate_alerts(
    conn: sqlite3.Connection,
    state: dict[str, float],
    snapshots: pd.DataFrame,
    holdings: pd.DataFrame,
    alert_loss_pct: float,
    alert_drawdown_pct: float,
    alert_gain_pct: float,
    max_line_pct: float,
) -> list[str]:
    fired: list[str] = []
    pnl_pct = safe_float(state.get("pnl_pct", 0.0), 0.0)
    drawdown_pct = compute_drawdown_pct(snapshots)
    if pnl_pct <= alert_loss_pct:
        if insert_alert(conn, "pnl_loss", "HIGH", "Perte seuil atteinte", f"Performance portefeuille: {pnl_pct:.2f}%"):
            fired.append("Perte seuil atteinte")
    if drawdown_pct <= alert_drawdown_pct:
        if insert_alert(conn, "drawdown", "HIGH", "Drawdown critique", f"Drawdown courant: {drawdown_pct:.2f}%"):
            fired.append("Drawdown critique")
    if pnl_pct >= alert_gain_pct:
        if insert_alert(conn, "pnl_gain", "INFO", "Objectif gain atteint", f"Performance portefeuille: {pnl_pct:.2f}%"):
            fired.append("Objectif gain atteint")
    if not holdings.empty and safe_float(state.get("portfolio_value", 0.0), 0.0) > 0:
        top = float(holdings["valeur_marche"].max() / state["portfolio_value"] * 100)
        if top > max_line_pct:
            if insert_alert(conn, "concentration_line", "MEDIUM", "Concentration excessive", f"Ligne max: {top:.2f}% (> {max_line_pct:.2f}%)."):
                fired.append("Concentration excessive")
    return fired


@st.cache_data(ttl=120, show_spinner=False)
def fetch_history_for_backtest(symbols: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    yf = _safe_yf_import()
    if not yf or not symbols:
        return pd.DataFrame()
    try:
        raw = yf.download(
            tickers=list(symbols),
            start=start,
            end=end,
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="column",
            threads=True,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            if "Adj Close" in raw.columns.get_level_values(0):
                close = raw["Adj Close"]
            else:
                close = raw["Close"]
        else:
            close = raw[["Adj Close"]] if "Adj Close" in raw.columns else raw[["Close"]]
            close.columns = [symbols[0]]
        close = close.dropna(how="all").ffill()
        return close
    except Exception:
        return pd.DataFrame()


def run_backtest(
    symbols: list[str],
    start: str,
    end: str,
    initial_capital: float,
    strategy: str = "buy_hold",
) -> tuple[pd.DataFrame, dict[str, float]]:
    prices = fetch_history_for_backtest(tuple(symbols), start, end)
    if prices.empty or prices.shape[1] == 0:
        return pd.DataFrame(), {}
    prices = prices.dropna(how="all").ffill().dropna()
    if prices.empty:
        return pd.DataFrame(), {}

    returns = prices.pct_change().fillna(0.0)
    if strategy == "sma50":
        signals = (prices > prices.rolling(50).mean()).astype(float).fillna(0.0)
        active = signals.sum(axis=1).replace(0, np.nan)
        weights = signals.div(active, axis=0).fillna(0.0)
    else:
        n = prices.shape[1]
        weights = pd.DataFrame(1 / n, index=prices.index, columns=prices.columns)

    port_ret = (weights.shift(1).fillna(weights.iloc[0]) * returns).sum(axis=1)
    equity = initial_capital * (1 + port_ret).cumprod()
    max_equity = equity.cummax()
    drawdown = equity / max_equity - 1.0
    out = pd.DataFrame({"equity": equity, "returns": port_ret, "drawdown": drawdown})
    annual_return = (equity.iloc[-1] / equity.iloc[0]) ** (252 / max(len(out), 1)) - 1
    vol = float(port_ret.std(ddof=1) * np.sqrt(252))
    sharpe = float((port_ret.mean() / port_ret.std(ddof=1) * np.sqrt(252)) if port_ret.std(ddof=1) > 0 else 0.0)
    metrics = {
        "annual_return_pct": float(annual_return * 100),
        "volatility_pct": float(vol * 100),
        "sharpe": sharpe,
        "max_drawdown_pct": float(drawdown.min() * 100),
        "final_value": float(equity.iloc[-1]),
    }
    return out, metrics


def structured_ai_recommendations(
    state: dict[str, float],
    opportunities: pd.DataFrame,
    vigilance: pd.DataFrame,
    risk_profile: str,
    max_line_pct: float,
) -> list[dict]:
    recs: list[dict] = []
    if not opportunities.empty:
        for row in opportunities.head(3).itertuples(index=False):
            recs.append(
                {
                    "action": "BUY",
                    "symbol": str(row.symbol),
                    "size_pct": min(max_line_pct * 0.4, 8.0),
                    "confidence": 0.66,
                    "rationale": f"Momentum positif 1M ({safe_float(row.ret_1m)*100:.1f}%) et volatilité maîtrisée.",
                    "risks": "Renversement de tendance court terme.",
                    "invalidation": "Si la volatilité 3M dépasse 45% ou si la performance 1M repasse négative.",
                }
            )
    if not vigilance.empty:
        for row in vigilance.head(2).itertuples(index=False):
            recs.append(
                {
                    "action": "REDUCE",
                    "symbol": str(row.symbol),
                    "size_pct": 3.0,
                    "confidence": 0.62,
                    "rationale": f"Signal de risque (1M {safe_float(row.ret_1m)*100:.1f}%, vol {safe_float(row.vol_3m)*100:.1f}%).",
                    "risks": "Perte d'opportunité si rebond rapide.",
                    "invalidation": "Si momentum 1M redevient > +3% et vol 3M < 35%.",
                }
            )
    if not recs:
        recs.append(
            {
                "action": "HOLD",
                "symbol": "PORTFOLIO",
                "size_pct": 0.0,
                "confidence": 0.5,
                "rationale": "Absence de signal directionnel fort.",
                "risks": "Sous-réaction en cas de choc marché.",
                "invalidation": "Si une alerte risque ou opportunité prioritaire apparaît.",
            }
        )
    return recs


def main() -> None:
setup_logger()
# --- Auth Base44 (JWT) ---
user = authenticate_user()
st.session_state["user_email"] = user["email"]
st.session_state["user_name"] = user.get("name", "")

# --- Per-user DB (isolation) ---
db_path = user_db_path(st.session_state["user_email"])
conn = get_connection(str(db_path))
    st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")
    render_css()
    universe_df = pd.DataFrame(ASSET_UNIVERSE)
    universe_symbols = sorted(universe_df["symbol"].unique().tolist())

    if "pending_snapshot_event" not in st.session_state:
        st.session_state["pending_snapshot_event"] = None
    if "assistant_output" not in st.session_state:
        st.session_state["assistant_output"] = ""
    if "live_enabled" not in st.session_state:
        st.session_state["live_enabled"] = get_setting(conn, "live_enabled", "1") == "1"
    if "refresh_seconds" not in st.session_state:
        raw = get_setting(conn, "refresh_seconds", str(DEFAULT_REFRESH_SECONDS))
        st.session_state["refresh_seconds"] = int(raw) if str(raw).isdigit() else DEFAULT_REFRESH_SECONDS
    if "realtime_symbols" not in st.session_state:
        stored = parse_symbols_csv(get_setting(conn, "realtime_symbols", ",".join(DEFAULT_REALTIME_SYMBOLS)), set(universe_symbols))
        st.session_state["realtime_symbols"] = stored or DEFAULT_REALTIME_SYMBOLS
    if "live_mode" not in st.session_state:
        mode = get_setting(conn, "live_mode", DEFAULT_LIVE_MODE).strip().lower()
        st.session_state["live_mode"] = mode if mode in {"polling", "websocket"} else DEFAULT_LIVE_MODE
    if "polygon_api_key" not in st.session_state:
        st.session_state["polygon_api_key"] = os.getenv("POLYGON_API_KEY", "").strip()
    if "base_currency" not in st.session_state:
        st.session_state["base_currency"] = get_setting(conn, "base_currency", DEFAULT_BASE_CURRENCY).upper()
    if "accounting_method" not in st.session_state:
        st.session_state["accounting_method"] = get_setting(conn, "accounting_method", DEFAULT_ACCOUNTING_METHOD).lower()
    if "snapshot_min_seconds" not in st.session_state:
        st.session_state["snapshot_min_seconds"] = get_setting_int(conn, "snapshot_min_seconds", DEFAULT_SNAPSHOT_MIN_SECONDS)
    if "snapshot_min_delta" not in st.session_state:
        st.session_state["snapshot_min_delta"] = get_setting_float(conn, "snapshot_min_delta", DEFAULT_SNAPSHOT_MIN_DELTA)
    if "ws_stale_seconds" not in st.session_state:
        st.session_state["ws_stale_seconds"] = get_setting_int(conn, "ws_stale_seconds", DEFAULT_WS_STALE_SECONDS)
    if "max_line_pct" not in st.session_state:
        st.session_state["max_line_pct"] = get_setting_float(conn, "max_line_pct", DEFAULT_MAX_LINE_PCT)
    if "max_sector_pct" not in st.session_state:
        st.session_state["max_sector_pct"] = get_setting_float(conn, "max_sector_pct", DEFAULT_MAX_SECTOR_PCT)
    if "max_zone_pct" not in st.session_state:
        st.session_state["max_zone_pct"] = get_setting_float(conn, "max_zone_pct", DEFAULT_MAX_ZONE_PCT)
    if "alert_loss_pct" not in st.session_state:
        st.session_state["alert_loss_pct"] = get_setting_float(conn, "alert_loss_pct", DEFAULT_ALERT_LOSS_PCT)
    if "alert_drawdown_pct" not in st.session_state:
        st.session_state["alert_drawdown_pct"] = get_setting_float(conn, "alert_drawdown_pct", DEFAULT_ALERT_DRAWDOWN_PCT)
    if "alert_gain_pct" not in st.session_state:
        st.session_state["alert_gain_pct"] = get_setting_float(conn, "alert_gain_pct", DEFAULT_ALERT_GAIN_PCT)
    if "alert_webhook_url" not in st.session_state:
        st.session_state["alert_webhook_url"] = get_setting(conn, "alert_webhook_url", os.getenv("PORTFOLIO_ALERT_WEBHOOK", ""))
    if "alert_email_to" not in st.session_state:
        st.session_state["alert_email_to"] = get_setting(conn, "alert_email_to", os.getenv("PORTFOLIO_ALERT_EMAIL", ""))
    if "last_structured_recs" not in st.session_state:
        st.session_state["last_structured_recs"] = []

    st.markdown(f"<div class='main-title'>{APP_TITLE}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='subtitle'>{APP_SUBTITLE}</div>", unsafe_allow_html=True)

    with st.sidebar:
        st.subheader("Configuration")
        initial_capital = float(get_setting(conn, "initial_capital", str(DEFAULT_INITIAL_CAPITAL)))
        exchange = get_setting(conn, "exchange", DEFAULT_EXCHANGE)
        new_initial_capital = st.number_input("Capital initial (€)", min_value=0.0, value=initial_capital, step=1000.0)
        new_exchange = st.selectbox("Exchange de référence", ["XNYS", "XPAR", "XHKG", "XTKS"], index=["XNYS", "XPAR", "XHKG", "XTKS"].index(exchange if exchange in {"XNYS", "XPAR", "XHKG", "XTKS"} else DEFAULT_EXCHANGE))
        base_currency = st.selectbox("Devise de valorisation", ["EUR", "USD", "GBP", "JPY", "CHF"], index=["EUR", "USD", "GBP", "JPY", "CHF"].index(st.session_state["base_currency"]) if st.session_state["base_currency"] in {"EUR","USD","GBP","JPY","CHF"} else 0)
        accounting_method = st.selectbox(
            "Méthode comptable",
            ["fifo", "lifo", "average"],
            index=["fifo", "lifo", "average"].index(st.session_state["accounting_method"]) if st.session_state["accounting_method"] in {"fifo","lifo","average"} else 0,
        )
        if st.button("Enregistrer la configuration", use_container_width=True):
            set_setting(conn, "initial_capital", str(new_initial_capital))
            set_setting(conn, "exchange", new_exchange)
            set_setting(conn, "base_currency", base_currency)
            set_setting(conn, "accounting_method", accounting_method)
            st.session_state["base_currency"] = base_currency
            st.session_state["accounting_method"] = accounting_method
            st.success("Configuration enregistrée.")

        st.subheader("Mode par défaut")
        st.caption("Profil stable et prêt à l'emploi. Aucun changement n'est appliqué tant que vous ne cliquez pas.")
        if st.button("Appliquer le mode par défaut (stable)", use_container_width=True):
            preset = apply_default_mode_settings(conn, universe_symbols)
            st.session_state["live_enabled"] = preset.get("live_enabled", "1") == "1"
            st.session_state["live_mode"] = preset.get("live_mode", DEFAULT_LIVE_MODE)
            st.session_state["refresh_seconds"] = int(float(preset.get("refresh_seconds", str(DEFAULT_REFRESH_SECONDS))))
            st.session_state["realtime_symbols"] = parse_symbols_csv(
                preset.get("realtime_symbols", symbols_to_csv(DEFAULT_REALTIME_SYMBOLS)),
                set(universe_symbols),
            )
            st.session_state["snapshot_min_seconds"] = int(
                float(preset.get("snapshot_min_seconds", str(DEFAULT_SNAPSHOT_MIN_SECONDS)))
            )
            st.session_state["snapshot_min_delta"] = float(
                preset.get("snapshot_min_delta", str(DEFAULT_SNAPSHOT_MIN_DELTA))
            )
            st.session_state["ws_stale_seconds"] = int(float(preset.get("ws_stale_seconds", str(DEFAULT_WS_STALE_SECONDS))))
            st.session_state["max_line_pct"] = float(preset.get("max_line_pct", str(DEFAULT_MAX_LINE_PCT)))
            st.session_state["max_sector_pct"] = float(preset.get("max_sector_pct", str(DEFAULT_MAX_SECTOR_PCT)))
            st.session_state["max_zone_pct"] = float(preset.get("max_zone_pct", str(DEFAULT_MAX_ZONE_PCT)))
            st.session_state["alert_loss_pct"] = float(preset.get("alert_loss_pct", str(DEFAULT_ALERT_LOSS_PCT)))
            st.session_state["alert_drawdown_pct"] = float(
                preset.get("alert_drawdown_pct", str(DEFAULT_ALERT_DRAWDOWN_PCT))
            )
            st.session_state["alert_gain_pct"] = float(preset.get("alert_gain_pct", str(DEFAULT_ALERT_GAIN_PCT)))
            fetch_quotes.clear()
            fetch_realtime_quotes.clear()
            st.success("Mode par défaut appliqué.")
            st.rerun()

        st.subheader("Flux Temps Réel")
        live_enabled = st.toggle("Activer cotation live", value=st.session_state["live_enabled"])
        live_mode_label = st.radio(
            "Mode live",
            ["Polling REST (Yahoo)", "WebSocket tick-by-tick (Polygon)"],
            index=0 if st.session_state["live_mode"] == "polling" else 1,
        )
        live_mode = "polling" if live_mode_label.startswith("Polling") else "websocket"
        min_refresh = 1 if live_mode == "websocket" else 5
        refresh_seconds = st.slider(
            "Fréquence de rafraîchissement UI (secondes)",
            min_value=min_refresh,
            max_value=60,
            value=max(min_refresh, int(st.session_state["refresh_seconds"])),
        )
        realtime_symbols = st.multiselect(
            "Actifs sélectionnés (live)",
            options=universe_symbols,
            default=[s for s in st.session_state["realtime_symbols"] if s in universe_symbols],
        )
        if live_mode == "websocket":
            polygon_api_key = st.text_input(
                "Clé API Polygon",
                value=st.session_state["polygon_api_key"],
                type="password",
                help="Utilise POLYGON_API_KEY ou colle la clé ici pour activer le flux tick-by-tick.",
            ).strip()
            st.session_state["polygon_api_key"] = polygon_api_key
            st.caption("Le streaming Polygon tick-by-tick couvre surtout les tickers US (ex: AAPL, SPY, MSFT).")
        if not realtime_symbols:
            realtime_symbols = DEFAULT_REALTIME_SYMBOLS.copy()

        st.subheader("Qualité Snapshots")
        snapshot_min_seconds = st.slider(
            "Intervalle min snapshots (sec)",
            min_value=1,
            max_value=120,
            value=int(st.session_state["snapshot_min_seconds"]),
        )
        snapshot_min_delta = st.number_input(
            "Seuil variation min snapshot",
            min_value=0.01,
            max_value=1000.0,
            value=float(st.session_state["snapshot_min_delta"]),
            step=0.5,
        )
        ws_stale_seconds = st.slider("Timeout flux WS (sec)", min_value=5, max_value=120, value=int(st.session_state["ws_stale_seconds"]))

        st.subheader("Contraintes Risque")
        max_line_pct = st.slider("Max par ligne (%)", min_value=5.0, max_value=100.0, value=float(st.session_state["max_line_pct"]))
        max_sector_pct = st.slider("Max par secteur (%)", min_value=10.0, max_value=100.0, value=float(st.session_state["max_sector_pct"]))
        max_zone_pct = st.slider("Max par zone (%)", min_value=10.0, max_value=100.0, value=float(st.session_state["max_zone_pct"]))

        st.subheader("Alertes")
        alert_loss_pct = st.number_input("Alerte perte (%)", min_value=-100.0, max_value=0.0, value=float(st.session_state["alert_loss_pct"]), step=0.5)
        alert_drawdown_pct = st.number_input("Alerte drawdown (%)", min_value=-100.0, max_value=0.0, value=float(st.session_state["alert_drawdown_pct"]), step=0.5)
        alert_gain_pct = st.number_input("Alerte gain (%)", min_value=0.0, max_value=500.0, value=float(st.session_state["alert_gain_pct"]), step=0.5)
        alert_webhook_url = st.text_input("Webhook alertes", value=st.session_state["alert_webhook_url"])
        alert_email_to = st.text_input("Email alertes", value=st.session_state["alert_email_to"])

        st.session_state["live_enabled"] = live_enabled
        st.session_state["refresh_seconds"] = refresh_seconds
        st.session_state["realtime_symbols"] = realtime_symbols
        st.session_state["live_mode"] = live_mode
        st.session_state["snapshot_min_seconds"] = snapshot_min_seconds
        st.session_state["snapshot_min_delta"] = snapshot_min_delta
        st.session_state["ws_stale_seconds"] = ws_stale_seconds
        st.session_state["max_line_pct"] = max_line_pct
        st.session_state["max_sector_pct"] = max_sector_pct
        st.session_state["max_zone_pct"] = max_zone_pct
        st.session_state["alert_loss_pct"] = alert_loss_pct
        st.session_state["alert_drawdown_pct"] = alert_drawdown_pct
        st.session_state["alert_gain_pct"] = alert_gain_pct
        st.session_state["alert_webhook_url"] = alert_webhook_url
        st.session_state["alert_email_to"] = alert_email_to
        set_setting(conn, "live_enabled", "1" if live_enabled else "0")
        set_setting(conn, "refresh_seconds", str(refresh_seconds))
        set_setting(conn, "realtime_symbols", symbols_to_csv(realtime_symbols))
        set_setting(conn, "live_mode", live_mode)
        set_setting(conn, "snapshot_min_seconds", str(snapshot_min_seconds))
        set_setting(conn, "snapshot_min_delta", str(snapshot_min_delta))
        set_setting(conn, "ws_stale_seconds", str(ws_stale_seconds))
        set_setting(conn, "max_line_pct", str(max_line_pct))
        set_setting(conn, "max_sector_pct", str(max_sector_pct))
        set_setting(conn, "max_zone_pct", str(max_zone_pct))
        set_setting(conn, "alert_loss_pct", str(alert_loss_pct))
        set_setting(conn, "alert_drawdown_pct", str(alert_drawdown_pct))
        set_setting(conn, "alert_gain_pct", str(alert_gain_pct))
        set_setting(conn, "alert_webhook_url", alert_webhook_url.strip())
        set_setting(conn, "alert_email_to", alert_email_to.strip())
        st.caption("Les transactions et snapshots sont persistés dans `data/portfolio_simulator.db`.")

    if st.session_state["live_enabled"] and st_autorefresh is not None:
        st_autorefresh(interval=int(st.session_state["refresh_seconds"]) * 1000, key="portfolio-live-refresh")

    transactions = load_transactions(conn)
    positions = compute_positions(transactions, accounting_method=st.session_state["accounting_method"])
    symbols_for_quotes = tuple(sorted(set(positions["symbol"].tolist() + st.session_state["realtime_symbols"])))
    quotes = pd.DataFrame(
        columns=[
            "symbol",
            "last",
            "previous",
            "change_pct",
            "quote_time_utc",
            "market_state",
            "currency",
            "source",
            "regular_price",
            "pre_price",
            "post_price",
            "official_close",
            "price_context",
        ]
    )
    stream_status = {"status": "disabled", "symbols": 0, "last_error": "", "last_message_utc": None, "last_tick_utc": None}
    polygon_stream = get_polygon_tick_stream()
    fallback_reason = ""
    ws_stale = False

    if not st.session_state["live_enabled"]:
        polygon_stream.stop()
    elif st.session_state["live_mode"] == "websocket":
        ws_symbols = [s for s in symbols_for_quotes if polygon_symbol_supported(s)]
        polygon_stream.configure(st.session_state["polygon_api_key"], ws_symbols)
        ws_quotes = polygon_stream.quotes_df()
        stream_status = polygon_stream.status()
        ws_stale = polygon_stream.is_stale(st.session_state["ws_stale_seconds"])
        if ws_stale:
            fallback_reason = f"flux WS stale > {st.session_state['ws_stale_seconds']}s"
        missing = tuple([s for s in symbols_for_quotes if s not in set(ws_quotes.get("symbol", pd.Series(dtype=str)).tolist())])
        if ws_stale:
            missing = symbols_for_quotes
        polled = fetch_realtime_quotes(missing) if missing else pd.DataFrame()
        quotes = merge_quotes(ws_quotes, polled, symbols_for_quotes)
    else:
        polygon_stream.stop()
        quotes = fetch_realtime_quotes(symbols_for_quotes)

    current_stream_signature = {
        "mode": st.session_state["live_mode"],
        "status": stream_status.get("status", "n/a"),
        "stale": ws_stale,
        "fallback": fallback_reason,
    }
    if st.session_state.get("last_stream_signature") != current_stream_signature:
        st.session_state["last_stream_signature"] = current_stream_signature
        log_event(conn, "INFO", "stream_status", current_stream_signature)

    base_currency = st.session_state["base_currency"]
    quote_currencies = []
    if not quotes.empty and "currency" in quotes.columns:
        quote_currencies = [infer_currency(str(r.symbol), str(r.currency), base_currency) for r in quotes.itertuples(index=False)]
    if not positions.empty and "currency" in positions.columns:
        quote_currencies += [str(c).upper() for c in positions["currency"].tolist()]
    fx_rates = fetch_fx_rates(base_currency, tuple(sorted(set(quote_currencies))))

    profiles = fetch_profiles(symbols_for_quotes)
    holdings, state = compute_portfolio_state(
        float(get_setting(conn, "initial_capital", str(DEFAULT_INITIAL_CAPITAL))),
        transactions,
        positions,
        quotes,
        profiles,
        base_currency=base_currency,
        fx_rates=fx_rates,
    )

    pending = st.session_state.get("pending_snapshot_event")
    if pending:
        upsert_snapshot(
            conn,
            state,
            explicit_event=pending.get("type"),
            explicit_label=pending.get("label"),
            min_delta_eur=float(st.session_state["snapshot_min_delta"]),
            min_seconds=int(st.session_state["snapshot_min_seconds"]),
        )
        st.session_state["pending_snapshot_event"] = None
    else:
        upsert_snapshot(
            conn,
            state,
            min_delta_eur=float(st.session_state["snapshot_min_delta"]),
            min_seconds=int(st.session_state["snapshot_min_seconds"]),
        )
    snapshots = load_snapshots(conn)

    fired_alerts = evaluate_alerts(
        conn=conn,
        state=state,
        snapshots=snapshots,
        holdings=holdings,
        alert_loss_pct=float(st.session_state["alert_loss_pct"]),
        alert_drawdown_pct=float(st.session_state["alert_drawdown_pct"]),
        alert_gain_pct=float(st.session_state["alert_gain_pct"]),
        max_line_pct=float(st.session_state["max_line_pct"]),
    )
    delivered_count = deliver_pending_alerts(
        conn=conn,
        webhook_url=st.session_state["alert_webhook_url"],
        email_to=st.session_state["alert_email_to"],
    )
    if fired_alerts:
        log_event(conn, "WARNING", "alerts_fired", {"alerts": fired_alerts})
    if delivered_count > 0:
        log_event(conn, "INFO", "alerts_delivered", {"count": delivered_count})

    tabs = st.tabs(["Synthèse", "Sélection d'Actifs", "Marchés", "Backtest & Ops", "Assistant Aide à la Décision"])

    with tabs[0]:
        status, market_subtitle, market_detail = create_market_clock_card(get_setting(conn, "exchange", DEFAULT_EXCHANGE))
        latest_quote = None
        if not quotes.empty and "quote_time_utc" in quotes.columns:
            latest_quote = pd.to_datetime(quotes["quote_time_utc"], errors="coerce", utc=True).max()
        if st.session_state["live_mode"] == "websocket":
            status_msg = str(stream_status.get("status", "n/a"))
            err = str(stream_status.get("last_error", "") or "")
            details = f"Mode: WebSocket Polygon ({status_msg})"
            if err:
                details += f" | Erreur: {err}"
            if fallback_reason:
                details += f" | Fallback REST: {fallback_reason}"
        else:
            details = "Mode: Polling REST Yahoo"
        live_line = "Cotation live indisponible pour le moment."
        if latest_quote is not None and not pd.isna(latest_quote):
            live_line = f"Dernière mise à jour: {to_display_time(latest_quote.isoformat())}"
        st.caption(f"{details} | {live_line} | Devise portefeuille: {state['base_currency']}")
        if fired_alerts:
            st.warning("Alertes actives: " + ", ".join(fired_alerts))

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            render_metric_card(
                title="Capital Total",
                value=money(state["portfolio_value"], state["base_currency"]),
                subtitle=f"Départ: {money(state['initial_capital'], state['base_currency'])}",
                primary=True,
                badge=pct(state["pnl_pct"]),
            )
        with c2:
            cash_pct = (state["cash"] / state["portfolio_value"] * 100) if state["portfolio_value"] else 0.0
            render_metric_card(title="Disponible", value=money(state["cash"], state["base_currency"]), subtitle=f"{cash_pct:.1f}% du capital")
        with c3:
            render_metric_card(title="Investissements", value=money(state["invested"], state["base_currency"]), subtitle=f"Marché: {status}")
        with c4:
            render_metric_card(
                title="Performance",
                value=money(state["pnl"], state["base_currency"]),
                subtitle=f"{market_subtitle}<br/>{market_detail}",
                badge=pct(state["pnl_pct"]),
            )

        st.markdown("")
        d1, d2 = st.columns([2, 1])
        with d1:
            annual = state["annual_dividends"]
            monthly = state["monthly_dividends"]
            render_metric_card(
                title="Revenus annuels estimés (dividendes)",
                value=money(annual, state["base_currency"]),
                subtitle=f"Par mois: {money(monthly, state['base_currency'])}",
            )
        with d2:
            st.metric("Transactions sauvegardées", f"{len(transactions)}")
            st.metric("Snapshots sauvegardés", f"{len(snapshots)}")
            if fx_rates:
                st.caption("FX live: " + ", ".join([f"1 {k} = {v:.4f} {state['base_currency']}" for k, v in fx_rates.items() if not np.isnan(v)]))

        st.plotly_chart(create_evolution_chart(snapshots, currency=state["base_currency"]), use_container_width=True)
        a1, a2 = st.columns(2)
        with a1:
            sector_alloc = holdings.groupby("secteur", as_index=False)["valeur_marche"].sum() if not holdings.empty else pd.DataFrame()
            st.plotly_chart(
                create_allocation_chart(sector_alloc, "secteur", "valeur_marche", "Répartition par secteur"),
                use_container_width=True,
            )
        with a2:
            geo_alloc = holdings.groupby("zone", as_index=False)["valeur_marche"].sum() if not holdings.empty else pd.DataFrame()
            st.plotly_chart(
                create_allocation_chart(geo_alloc, "zone", "valeur_marche", "Répartition par zone géographique"),
                use_container_width=True,
            )

    with tabs[1]:
        st.subheader("Univers d'actifs")
        region_tabs = st.tabs(["USA", "Europe", "Asie", "Pays émergent"])
        for region, region_tab in zip(["USA", "Europe", "Asie", "Pays émergent"], region_tabs):
            with region_tab:
                region_df = universe_df[universe_df["zone"] == region][["symbol", "name", "asset_type", "sector"]]
                st.dataframe(region_df, use_container_width=True, hide_index=True)

        st.markdown("#### Métaux précieux et terres rares")
        metals_df = universe_df[universe_df["asset_type"].isin(["Métal précieux", "Terres rares"])][
            ["symbol", "name", "asset_type", "zone", "sector"]
        ]
        st.dataframe(metals_df, use_container_width=True, hide_index=True)

        st.markdown("#### Prix unitaires live des actifs sélectionnés")
        live_watch = quotes[quotes["symbol"].isin(st.session_state["realtime_symbols"])].copy() if not quotes.empty else pd.DataFrame()
        if live_watch.empty:
            st.info("Aucune cotation live disponible pour la sélection actuelle.")
        else:
            live_watch["maj"] = live_watch["quote_time_utc"].apply(to_display_time)
            live_watch["variation_%"] = live_watch["change_pct"].round(2)
            st.dataframe(
                live_watch[
                    ["symbol", "last", "previous", "official_close", "variation_%", "market_state", "price_context", "currency", "maj", "source"]
                ].rename(
                    columns={
                        "symbol": "Ticker",
                        "last": "Prix unitaire",
                        "previous": "Précédent",
                        "official_close": "Clôture officielle",
                        "variation_%": "Variation %",
                        "market_state": "État marché",
                        "price_context": "Contexte prix",
                        "currency": "Devise",
                        "maj": "Dernière maj",
                        "source": "Source API",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### Exécuter une transaction")
        qmap = quotes.set_index("symbol").to_dict(orient="index") if not quotes.empty else {}
        held_qty = holdings.set_index("symbol")["quantite"].to_dict() if not holdings.empty else {}
        available_symbols = sorted(set(universe_df["symbol"].tolist() + list(held_qty.keys())))
        with st.form("trade_form"):
            t1, t2, t3, t4 = st.columns(4)
            with t1:
                side = st.selectbox("Action", ["BUY", "SELL"], format_func=lambda x: "Achat" if x == "BUY" else "Vente")
            with t2:
                symbol = st.selectbox("Actif", available_symbols, index=0)
            with t3:
                default_price = float(qmap.get(symbol, {}).get("last", 0.0))
                price = st.number_input("Prix unitaire", min_value=0.0, value=default_price, step=0.01)
            with t4:
                quantity = st.number_input("Quantité", min_value=0.0, value=1.0, step=1.0)
            x1, x2, x3 = st.columns([1, 1, 2])
            with x1:
                fees = st.number_input("Frais", min_value=0.0, value=0.0, step=0.01)
            with x2:
                trade_exchange = st.selectbox("Marché", ["XNYS", "XPAR", "XHKG", "XTKS"], index=0)
            with x3:
                note = st.text_input("Note (optionnelle)")
            strategy_tag = st.text_input("Tag stratégie", value="manual")
            submitted = st.form_submit_button("Enregistrer la transaction", use_container_width=True)
            if submitted:
                quote_currency = infer_currency(symbol, str(qmap.get(symbol, {}).get("currency", "")), state["base_currency"])
                fx_to_base = safe_float(fx_rates.get(quote_currency, np.nan), np.nan)
                if np.isnan(fx_to_base) or fx_to_base <= 0:
                    fx_to_base = 1.0 if quote_currency == state["base_currency"] else safe_float(qmap.get(symbol, {}).get("fx_to_base", 1.0), 1.0)
                if quantity <= 0:
                    st.error("La quantité doit être positive.")
                elif side == "SELL" and quantity > float(held_qty.get(symbol, 0.0)):
                    st.error("Quantité vendue supérieure à la position détenue.")
                elif price <= 0:
                    st.error("Le prix doit être strictement positif.")
                else:
                    risk_errors = check_trade_risk(
                        side=side,
                        symbol=symbol,
                        quantity=float(quantity),
                        price=float(price),
                        fees=float(fees),
                        cash=float(state["cash"]),
                        holdings=holdings,
                        base_currency=state["base_currency"],
                        fx_to_base=float(fx_to_base),
                        max_line_pct=float(st.session_state["max_line_pct"]),
                        max_sector_pct=float(st.session_state["max_sector_pct"]),
                        max_zone_pct=float(st.session_state["max_zone_pct"]),
                    )
                    if risk_errors:
                        for err in risk_errors:
                            st.error(err)
                        log_event(conn, "WARNING", "trade_blocked_risk", {"symbol": symbol, "side": side, "errors": risk_errors})
                    else:
                        insert_transaction(
                            conn,
                            symbol=symbol,
                            side=side,
                            quantity=float(quantity),
                            price=float(price),
                            fees=float(fees),
                            currency=quote_currency,
                            fx_to_base=float(fx_to_base),
                            exchange=trade_exchange,
                            strategy_tag=strategy_tag,
                            note=note,
                        )
                        log_event(
                            conn,
                            "INFO",
                            "trade_inserted",
                            {
                                "symbol": symbol,
                                "side": side,
                                "qty": float(quantity),
                                "price": float(price),
                                "currency": quote_currency,
                                "fx_to_base": float(fx_to_base),
                            },
                        )
                        label = f"{'Achat' if side == 'BUY' else 'Vente'} {quantity:g} {symbol} @ {price:.2f} {quote_currency}"
                        st.session_state["pending_snapshot_event"] = {"type": side, "label": label}
                        fetch_quotes.clear()
                        fetch_realtime_quotes.clear()
                        st.success("Transaction enregistrée.")
                        st.rerun()

        st.markdown("#### Positions en portefeuille")
        if holdings.empty:
            st.info("Aucune position ouverte.")
        else:
            view = holdings[
                [
                    "symbol",
                    "nom",
                    "zone",
                    "secteur",
                    "type",
                    "quantite",
                    "prix_moyen",
                    "cours",
                    "devise",
                    "fx_to_base",
                    "valeur_marche_devise",
                    "valeur_marche",
                    "pnl_latent",
                    "pnl_realise",
                ]
            ].copy()
            st.dataframe(
                view.rename(
                    columns={
                        "symbol": "Ticker",
                        "nom": "Nom",
                        "zone": "Zone",
                        "secteur": "Secteur",
                        "type": "Type",
                        "quantite": "Quantité",
                        "prix_moyen": "Prix moyen",
                        "cours": "Cours",
                        "devise": "Devise",
                        "fx_to_base": f"FX->{state['base_currency']}",
                        "valeur_marche_devise": "Valeur devise",
                        "valeur_marche": "Valeur marché",
                        "pnl_latent": "PnL latent",
                        "pnl_realise": "PnL réalisé",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    with tabs[2]:
        st.subheader("Analyse des marchés")
        selected = st.session_state["realtime_symbols"]
        st.caption("La sélection des actifs live se pilote dans la barre latérale.")
        if st.session_state["live_mode"] == "websocket":
            st.caption(
                f"Flux WebSocket Polygon: {stream_status.get('status', 'n/a')} | "
                f"Tickers streamés: {stream_status.get('symbols', 0)}"
            )
        market_quotes = quotes[quotes["symbol"].isin(selected)].copy() if not quotes.empty else pd.DataFrame()
        if market_quotes.empty:
            st.warning("Impossible de charger les cotations en temps réel pour le moment.")
        else:
            table = market_quotes.copy()
            table["variation_%"] = table["change_pct"].round(2)
            st.dataframe(
                table[
                    ["symbol", "last", "previous", "official_close", "variation_%", "market_state", "price_context", "currency", "source"]
                ].rename(
                    columns={
                        "symbol": "Ticker",
                        "last": "Dernier",
                        "previous": "Précédent",
                        "official_close": "Clôture officielle",
                        "variation_%": "Variation %",
                        "market_state": "État marché",
                        "price_context": "Contexte prix",
                        "currency": "Devise",
                        "source": "Source API",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        metrics = fetch_signal_metrics(tuple(selected))
        opportunities, vigilance = opportunities_and_vigilance(metrics)
        o1, o2 = st.columns(2)
        with o1:
            st.markdown("#### Opportunités")
            if opportunities.empty:
                st.info("Aucune opportunité détectée selon les règles de momentum/volatilité.")
            else:
                show = opportunities.copy()
                show["ret_1m"] = (show["ret_1m"] * 100).round(2)
                show["ret_3m"] = (show["ret_3m"] * 100).round(2)
                show["vol_3m"] = (show["vol_3m"] * 100).round(2)
                st.dataframe(
                    show.rename(columns={"symbol": "Ticker", "ret_1m": "Perf 1m %", "ret_3m": "Perf 3m %", "vol_3m": "Vol 3m %"}),
                    use_container_width=True,
                    hide_index=True,
                )
        with o2:
            st.markdown("#### Points de vigilance")
            if vigilance.empty:
                st.info("Aucun signal de vigilance majeur détecté.")
            else:
                show = vigilance.copy()
                show["ret_1m"] = (show["ret_1m"] * 100).round(2)
                show["ret_3m"] = (show["ret_3m"] * 100).round(2)
                show["vol_3m"] = (show["vol_3m"] * 100).round(2)
                st.dataframe(
                    show.rename(columns={"symbol": "Ticker", "ret_1m": "Perf 1m %", "ret_3m": "Perf 3m %", "vol_3m": "Vol 3m %"}),
                    use_container_width=True,
                    hide_index=True,
                )

        st.markdown("#### Contexte géopolitique")
        news = fetch_geopolitical_news(max_items=8)
        risk_level, risk_score = compute_geopolitical_risk(news)
        st.metric("Risque géopolitique agrégé", f"{risk_level} (score {risk_score})")
        if not news:
            st.caption("Flux d'actualités indisponible.")
        else:
            for item in news:
                title = item.get("title", "Sans titre")
                link = item.get("link", "")
                published = item.get("published", "")
                st.markdown(f"- [{title}]({link})  \n  `{published}`")

    with tabs[3]:
        st.subheader("Backtest & Opérations")
        st.caption("Simulation historique, replay des snapshots, alertes et logs techniques.")

        bt1, bt2, bt3 = st.columns(3)
        with bt1:
            bt_strategy = st.selectbox("Stratégie", ["buy_hold", "sma50"], format_func=lambda x: "Buy & Hold équipondéré" if x == "buy_hold" else "SMA50 dynamique")
        with bt2:
            bt_start = st.date_input("Début", value=pd.Timestamp.now(tz="UTC").date() - pd.Timedelta(days=365 * 3))
        with bt3:
            bt_end = st.date_input("Fin", value=pd.Timestamp.now(tz="UTC").date())
        bt_symbols = st.multiselect("Actifs backtest", options=universe_symbols, default=st.session_state["realtime_symbols"])
        bt_capital = st.number_input("Capital initial backtest", min_value=1.0, value=float(state["initial_capital"]), step=1000.0)
        if st.button("Lancer backtest", use_container_width=True):
            if bt_start >= bt_end:
                st.error("La date de début doit être antérieure à la date de fin.")
            else:
                curve, metrics = run_backtest(
                    symbols=bt_symbols or st.session_state["realtime_symbols"],
                    start=str(bt_start),
                    end=str(bt_end),
                    initial_capital=float(bt_capital),
                    strategy=bt_strategy,
                )
                if curve.empty:
                    st.error("Backtest indisponible (données manquantes).")
                else:
                    conn.execute(
                        """
                        INSERT INTO backtest_runs(created_at_utc, strategy, symbols_csv, start_date, end_date, initial_capital, metrics_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            utc_now_iso(),
                            bt_strategy,
                            symbols_to_csv(bt_symbols or st.session_state["realtime_symbols"]),
                            str(bt_start),
                            str(bt_end),
                            float(bt_capital),
                            json.dumps(metrics, ensure_ascii=False),
                        ),
                    )
                    conn.commit()
                    log_event(conn, "INFO", "backtest_run", {"strategy": bt_strategy, "symbols": bt_symbols})
                    st.success("Backtest enregistré.")
                    st.dataframe(pd.DataFrame([metrics]), use_container_width=True, hide_index=True)
                    fig_bt = go.Figure()
                    fig_bt.add_trace(go.Scatter(x=curve.index, y=curve["equity"], mode="lines", name="Equity"))
                    fig_bt.add_trace(go.Scatter(x=curve.index, y=curve["drawdown"] * 100, mode="lines", name="Drawdown %", yaxis="y2"))
                    fig_bt.update_layout(
                        title="Backtest - Courbe de capital et drawdown",
                        yaxis_title=f"Capital ({state['base_currency']})",
                        yaxis2={"overlaying": "y", "side": "right", "title": "Drawdown %"},
                        template="plotly_white",
                    )
                    st.plotly_chart(fig_bt, use_container_width=True)

        st.markdown("#### Replay snapshots")
        if snapshots.empty:
            st.info("Aucun snapshot à rejouer.")
        else:
            replay_idx = st.slider("Point dans l'historique", min_value=0, max_value=len(snapshots) - 1, value=len(snapshots) - 1)
            row = snapshots.iloc[replay_idx]
            st.write(
                f"Date: {to_display_time(str(row['captured_at_utc']))} | "
                f"Valeur: {money(float(row['portfolio_value']), state['base_currency'])} | "
                f"Événement: {row.get('event_type', '')} {row.get('event_label', '')}"
            )

        st.markdown("#### Alertes récentes")
        alerts_df = load_recent_alerts(conn, limit=20)
        if alerts_df.empty:
            st.caption("Aucune alerte enregistrée.")
        else:
            st.dataframe(alerts_df[["created_at_utc", "severity", "title", "message", "delivered"]], use_container_width=True, hide_index=True)

        st.markdown("#### Logs techniques")
        logs_df = load_recent_logs(conn, limit=60)
        if logs_df.empty:
            st.caption("Aucun log applicatif.")
        else:
            st.dataframe(logs_df, use_container_width=True, hide_index=True)

    with tabs[4]:
        st.subheader("Assistant IA d'aide à la décision")
        st.caption("Mode local activé par défaut. Si `OPENAI_API_KEY` est défini, vous pouvez activer la génération LLM.")

        risk_profile = st.selectbox("Profil de risque", ["Prudent", "Équilibré", "Dynamique"], index=1)
        horizon = st.slider("Horizon d'investissement (années)", min_value=1, max_value=20, value=8)
        objective = st.text_area("Objectif principal", value="Construire un portefeuille diversifié, robuste aux cycles macro.")
        question = st.text_area("Question spécifique", value="Quels ajustements faire cette semaine compte tenu du contexte actuel ?")
        use_openai = st.checkbox("Utiliser l'API IA (OpenAI) si disponible", value=False)

        latest_metrics = fetch_signal_metrics(tuple(sorted(set(universe_df["symbol"].tolist()))))
        ops, vig = opportunities_and_vigilance(latest_metrics)
        geo_news = fetch_geopolitical_news(max_items=8)
        geo_level, _ = compute_geopolitical_risk(geo_news)

        if st.button("Générer une recommandation", use_container_width=True):
            structured = structured_ai_recommendations(
                state=state,
                opportunities=ops,
                vigilance=vig,
                risk_profile=risk_profile,
                max_line_pct=float(st.session_state["max_line_pct"]),
            )
            local_answer = local_ai_assistant(
                objective=objective,
                question=question,
                profile=risk_profile,
                horizon_years=horizon,
                state=state,
                opportunities=ops,
                vigilance=vig,
                geo_risk_level=geo_level,
            )
            if use_openai:
                prompt = (
                    f"Objectif: {objective}\nQuestion: {question}\nProfil: {risk_profile}\nHorizon: {horizon} ans\n"
                    f"Portefeuille: valeur={state['portfolio_value']:.2f}, pnl={state['pnl']:.2f}, pnl_pct={state['pnl_pct']:.2f}%\n"
                    f"Risque géopolitique: {geo_level}\n"
                    f"Opportunités: {ops[['symbol','ret_1m','vol_3m']].to_dict(orient='records') if not ops.empty else []}\n"
                    f"Vigilance: {vig[['symbol','ret_1m','vol_3m']].to_dict(orient='records') if not vig.empty else []}\n"
                    "Réponds en JSON strict avec clé `recommendations` (liste d'objets: action,symbol,size_pct,confidence,rationale,risks,invalidation) "
                    "et clé `summary` (texte court en français)."
                )
                llm_answer = openai_ai_assistant(prompt)
                parsed = None
                if llm_answer:
                    try:
                        parsed = json.loads(llm_answer)
                    except Exception:
                        parsed = None
                if parsed and isinstance(parsed, dict):
                    st.session_state["assistant_output"] = str(parsed.get("summary", local_answer))
                    llm_recs = parsed.get("recommendations")
                    if isinstance(llm_recs, list) and llm_recs:
                        st.session_state["last_structured_recs"] = llm_recs
                    else:
                        st.session_state["last_structured_recs"] = structured
                else:
                    st.session_state["assistant_output"] = llm_answer or local_answer
                    st.session_state["last_structured_recs"] = structured
            else:
                st.session_state["assistant_output"] = local_answer
                st.session_state["last_structured_recs"] = structured
            log_event(conn, "INFO", "ai_recommendation_generated", {"use_openai": use_openai, "profile": risk_profile})

        if st.session_state["assistant_output"]:
            st.markdown("#### Recommandation")
            st.write(st.session_state["assistant_output"])
        if st.session_state["last_structured_recs"]:
            st.markdown("#### Plan d'actions structuré")
            st.dataframe(pd.DataFrame(st.session_state["last_structured_recs"]), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
