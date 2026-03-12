"""Constantes globales du simulateur Liberty Bourse."""
from __future__ import annotations

import threading
from zoneinfo import ZoneInfo
from pathlib import Path

APP_TITLE = "Liberty Bourse"
APP_SUBTITLE = "Suivi dynamique, répartition géographique/sectorielle et assistant d'aide à la décision"
APP_PUBLIC_URL = "https://libertybourse-kbussevqm2stdwmrqbxkf4.streamlit.app"
MAIN_TAB_LABELS = ["Synthèse", "Sélection d'Actifs", "Marchés", "Simulation & Opérations", "Assistant Aide à la Décision"]
AUTO_REFRESH_ALLOWED_TABS = {"Synthèse", "Marchés"}
DEFAULT_INITIAL_CAPITAL = 100_000.0
DEFAULT_EXCHANGE = "TRLS"
EXCHANGE_OPTIONS = ["TRLS", "XPAR", "XNYS", "XTKS", "XHKG"]
EXCHANGE_LABELS = {
    "TRLS": "TRLS (Trade Republic)",
    "XPAR": "XPAR (Paris)",
    "XNYS": "XNYS (New York)",
    "XTKS": "XTKS (Tokyo)",
    "XHKG": "XHKG (Hong Kong)",
}
EXCHANGE_HOURS = {
    "TRLS": "07:30-23:00",
    "XPAR": "09:00-17:30",
    "XNYS": "09:30-16:00",
    "XTKS": "09:00-15:00",
    "XHKG": "09:30-16:00",
}
DEFAULT_REFRESH_SECONDS = 10
DEFAULT_REALTIME_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "GLD", "EEM"]
DEFAULT_LIVE_MODE = "polling"
DEFAULT_BASE_CURRENCY = "EUR"
DEFAULT_ACCOUNTING_METHOD = "fifo"
DEFAULT_SNAPSHOT_MIN_SECONDS = 10
DEFAULT_SNAPSHOT_MIN_DELTA = 0.01  # Abaissé de 1.0 à 0.01 pour capturer toute variation
DEFAULT_WS_STALE_SECONDS = 20
DEFAULT_MAX_LINE_PCT = 25.0
DEFAULT_MAX_SECTOR_PCT = 45.0
DEFAULT_MAX_ZONE_PCT = 55.0
DEFAULT_ALERT_LOSS_PCT = -7.0
DEFAULT_ALERT_DRAWDOWN_PCT = -10.0
DEFAULT_ALERT_GAIN_PCT = 10.0
DISPLAY_TZ = ZoneInfo("Europe/Paris")
DB_PATH = Path("data/portfolio_simulator.db")
LOG_PATH = Path("output/portfolio_app.log")
ALERT_COOLDOWN_SECONDS = 600
DEFAULT_AUTH_MODE = "required"
JWT_DEFAULT_LEEWAY_SECONDS = 30
JWT_MAX_TOKEN_LENGTH = 8_192
DEFAULT_BENCHMARK_SYMBOL = "SPY"
DEFAULT_SIM_SLIPPAGE_BPS = 5.0
DEFAULT_SIM_SPREAD_BPS = 2.0
DEFAULT_SIM_PARTIAL_MIN = 0.55
DEFAULT_SIM_PARTIAL_MAX = 1.0
POSITION_DUST_EPSILON = 1e-6

API_PROVIDERS = ["polygon_ws_tick", "yahoo_quote_api", "yfinance_history"]
PROVIDER_HEALTH_LOCK = threading.Lock()
PROVIDER_HEALTH: dict[str, dict[str, float | str]] = {
    p: {
        "success": 0.0,
        "error": 0.0,
        "consecutive_error": 0.0,
        "circuit_open_until": 0.0,
        "last_error": "",
        "last_error_utc": "",
    }
    for p in API_PROVIDERS
}
PROVIDER_RATE_LOCK = threading.Lock()
PROVIDER_LAST_CALL_TS: dict[str, float] = {}
PROVIDER_MIN_INTERVAL_SECONDS = {
    "yahoo_quote_api": 0.2,
    "yfinance_history": 0.25,
    "polygon_ws_tick": 0.0,
}
API_MAX_RETRIES = 3
API_BACKOFF_BASE_SECONDS = 0.35
API_CIRCUIT_BREAKER_ERRORS = 3
API_CIRCUIT_BREAKER_SECONDS = 25
SSL_ERROR_MARKERS = ("CERTIFICATE_VERIFY_FAILED", "SSL", "TLS")

EVENT_COLORS = {
    "INIT": "#9ca3af",
    "BUY": "#2563eb",
    "SELL": "#ef4444",
    "UP": "#16a34a",
    "DOWN": "#dc2626",
}

COUNTRY_TO_ZONE = {
    "United States": "USA", "USA": "USA",
    "France": "Europe", "Germany": "Europe", "United Kingdom": "Europe",
    "Switzerland": "Europe", "Netherlands": "Europe", "Italy": "Europe", "Spain": "Europe",
    "China": "Asie", "Japan": "Asie", "South Korea": "Asie", "Hong Kong": "Asie", "Taiwan": "Asie",
    "India": "Pays émergent", "Brazil": "Pays émergent", "Mexico": "Pays émergent",
    "Indonesia": "Pays émergent", "South Africa": "Pays émergent", "Turkey": "Pays émergent",
    "Chile": "Pays émergent", "Peru": "Pays émergent",
}

RISK_KEYWORDS = {
    "war": 4, "guerre": 4, "sanction": 3, "conflit": 3, "conflict": 3,
    "tariff": 2, "douane": 2, "attack": 3, "attaque": 3, "embargo": 3,
    "oil": 1, "petrol": 1, "taiwan": 2, "middle east": 2, "ukraine": 2,
}

# ──────────────────────────────────────────────────────────────
# Mapping des suffixes de ticker → devise (CORRIGÉ)
# ──────────────────────────────────────────────────────────────
TICKER_SUFFIX_CURRENCY: dict[str, str] = {
    ".PA": "EUR",
    ".AS": "EUR",
    ".DE": "EUR",
    ".MI": "EUR",
    ".MC": "EUR",
    ".BR": "EUR",
    ".T": "JPY",
    ".HK": "HKD",
    ".SW": "CHF",
    ".L": "GBP",
    ".KS": "KRW",
    ".TW": "TWD",
    ".AX": "AUD",
    ".TO": "CAD",
    ".SA": "BRL",
}
