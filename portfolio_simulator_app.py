from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import json
import os
import re
import sqlite3
import smtplib
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from portfolio_tool.data import get_market_clock, filter_prices_to_market_sessions

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover - fallback runtime
    st_autorefresh = None


APP_TITLE = "Simulateur de Portefeuille Boursier"
APP_SUBTITLE = "Suivi dynamique, répartition géographique/sectorielle et assistant d'aide à la décision"
MAIN_TAB_LABELS = ["Synthèse", "Sélection d'Actifs", "Marchés", "Simulation & Opérations", "Assistant Aide à la Décision"]
AUTO_REFRESH_ALLOWED_TABS = {"Synthèse", "Marchés"}
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
LOG_PATH = Path("output/portfolio_app.log")
ALERT_COOLDOWN_SECONDS = 600
DEFAULT_AUTH_MODE = "required"  # optional | required | off
JWT_DEFAULT_LEEWAY_SECONDS = 30
JWT_MAX_TOKEN_LENGTH = 8_192
DEFAULT_BENCHMARK_SYMBOL = "SPY"
DEFAULT_SIM_SLIPPAGE_BPS = 5.0
DEFAULT_SIM_SPREAD_BPS = 2.0
DEFAULT_SIM_PARTIAL_MIN = 0.55
DEFAULT_SIM_PARTIAL_MAX = 1.0

API_PROVIDERS = ["polygon_ws_tick", "yahoo_quote_api", "yfinance_history", "yahoo_fx"]
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
    "yahoo_fx": 0.2,
    "yfinance_history": 0.25,
    "polygon_ws_tick": 0.0,
}
API_MAX_RETRIES = 3
API_BACKOFF_BASE_SECONDS = 0.35
API_CIRCUIT_BREAKER_ERRORS = 3
API_CIRCUIT_BREAKER_SECONDS = 25

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
    {"symbol": "AMZN", "name": "Amazon", "asset_type": "Action", "zone": "USA", "sector": "Consommation"},
    {"symbol": "GOOGL", "name": "Alphabet", "asset_type": "Action", "zone": "USA", "sector": "Communication"},
    {"symbol": "META", "name": "Meta", "asset_type": "Action", "zone": "USA", "sector": "Communication"},
    {"symbol": "TSLA", "name": "Tesla", "asset_type": "Action", "zone": "USA", "sector": "Automobile"},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway B", "asset_type": "Action", "zone": "USA", "sector": "Finance"},
    {"symbol": "JPM", "name": "JPMorgan", "asset_type": "Action", "zone": "USA", "sector": "Finance"},
    {"symbol": "V", "name": "Visa", "asset_type": "Action", "zone": "USA", "sector": "Finance"},
    {"symbol": "XOM", "name": "ExxonMobil", "asset_type": "Action", "zone": "USA", "sector": "Energie"},
    {"symbol": "JNJ", "name": "Johnson & Johnson", "asset_type": "Action", "zone": "USA", "sector": "Santé"},
    {"symbol": "UNH", "name": "UnitedHealth", "asset_type": "Action", "zone": "USA", "sector": "Santé"},
    {"symbol": "PG", "name": "Procter & Gamble", "asset_type": "Action", "zone": "USA", "sector": "Consommation"},
    {"symbol": "KO", "name": "Coca-Cola", "asset_type": "Action", "zone": "USA", "sector": "Consommation"},
    {"symbol": "COST", "name": "Costco", "asset_type": "Action", "zone": "USA", "sector": "Distribution"},
    {"symbol": "AVGO", "name": "Broadcom", "asset_type": "Action", "zone": "USA", "sector": "Semi-conducteurs"},
    {"symbol": "AMD", "name": "AMD", "asset_type": "Action", "zone": "USA", "sector": "Semi-conducteurs"},
    {"symbol": "CRM", "name": "Salesforce", "asset_type": "Action", "zone": "USA", "sector": "Logiciels"},
    {"symbol": "NFLX", "name": "Netflix", "asset_type": "Action", "zone": "USA", "sector": "Communication"},
    {"symbol": "SPY", "name": "S&P 500 ETF", "asset_type": "ETF", "zone": "USA", "sector": "Indice large cap"},
    {"symbol": "QQQ", "name": "Nasdaq 100 ETF", "asset_type": "ETF", "zone": "USA", "sector": "Technologie"},
    {"symbol": "DIA", "name": "Dow Jones ETF", "asset_type": "ETF", "zone": "USA", "sector": "Indice large cap"},
    {"symbol": "IWM", "name": "Russell 2000 ETF", "asset_type": "ETF", "zone": "USA", "sector": "Small caps"},
    {"symbol": "VTI", "name": "Total US Market ETF", "asset_type": "ETF", "zone": "USA", "sector": "Indice large cap"},
    {"symbol": "XLK", "name": "Technology Select ETF", "asset_type": "ETF", "zone": "USA", "sector": "Technologie"},
    {"symbol": "XLF", "name": "Financial Select ETF", "asset_type": "ETF", "zone": "USA", "sector": "Finance"},
    {"symbol": "XLE", "name": "Energy Select ETF", "asset_type": "ETF", "zone": "USA", "sector": "Energie"},
    {"symbol": "XLV", "name": "Health Care Select ETF", "asset_type": "ETF", "zone": "USA", "sector": "Santé"},
    {"symbol": "VNQ", "name": "US REIT ETF", "asset_type": "ETF", "zone": "USA", "sector": "Immobilier"},
    {"symbol": "TLT", "name": "US 20Y Treasury ETF", "asset_type": "ETF", "zone": "USA", "sector": "Obligations"},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "asset_type": "Métal précieux", "zone": "USA", "sector": "Or"},
    {"symbol": "IAU", "name": "iShares Gold Trust", "asset_type": "Métal précieux", "zone": "USA", "sector": "Or"},
    {"symbol": "SLV", "name": "iShares Silver Trust", "asset_type": "Métal précieux", "zone": "USA", "sector": "Argent"},
    {"symbol": "PPLT", "name": "abrdn Physical Platinum", "asset_type": "Métal précieux", "zone": "USA", "sector": "Platine"},
    {"symbol": "PALL", "name": "abrdn Physical Palladium", "asset_type": "Métal précieux", "zone": "USA", "sector": "Palladium"},
    {"symbol": "GDX", "name": "Gold Miners ETF", "asset_type": "Métal précieux", "zone": "USA", "sector": "Or"},
    {"symbol": "SIL", "name": "Silver Miners ETF", "asset_type": "Métal précieux", "zone": "USA", "sector": "Argent"},
    {"symbol": "REMX", "name": "Rare Earth / Strategic Metals ETF", "asset_type": "Terres rares", "zone": "USA", "sector": "Métaux stratégiques"},
    {"symbol": "LIT", "name": "Lithium & Battery Tech ETF", "asset_type": "Terres rares", "zone": "USA", "sector": "Lithium"},
    {"symbol": "MC.PA", "name": "LVMH", "asset_type": "Action", "zone": "Europe", "sector": "Luxe"},
    {"symbol": "SAN.PA", "name": "Sanofi", "asset_type": "Action", "zone": "Europe", "sector": "Santé"},
    {"symbol": "ASML.AS", "name": "ASML", "asset_type": "Action", "zone": "Europe", "sector": "Semi-conducteurs"},
    {"symbol": "AI.PA", "name": "Air Liquide", "asset_type": "Action", "zone": "Europe", "sector": "Industrie"},
    {"symbol": "OR.PA", "name": "L'Oréal", "asset_type": "Action", "zone": "Europe", "sector": "Consommation"},
    {"symbol": "DG.PA", "name": "Vinci", "asset_type": "Action", "zone": "Europe", "sector": "Industrie"},
    {"symbol": "BN.PA", "name": "Danone", "asset_type": "Action", "zone": "Europe", "sector": "Consommation"},
    {"symbol": "SU.PA", "name": "Schneider Electric", "asset_type": "Action", "zone": "Europe", "sector": "Industrie"},
    {"symbol": "TTE.PA", "name": "TotalEnergies", "asset_type": "Action", "zone": "Europe", "sector": "Energie"},
    {"symbol": "BNP.PA", "name": "BNP Paribas", "asset_type": "Action", "zone": "Europe", "sector": "Finance"},
    {"symbol": "AIR.PA", "name": "Airbus", "asset_type": "Action", "zone": "Europe", "sector": "Aéronautique"},
    {"symbol": "RNO.PA", "name": "Renault", "asset_type": "Action", "zone": "Europe", "sector": "Automobile"},
    {"symbol": "SAP.DE", "name": "SAP", "asset_type": "Action", "zone": "Europe", "sector": "Logiciels"},
    {"symbol": "SIE.DE", "name": "Siemens", "asset_type": "Action", "zone": "Europe", "sector": "Industrie"},
    {"symbol": "VOW3.DE", "name": "Volkswagen", "asset_type": "Action", "zone": "Europe", "sector": "Automobile"},
    {"symbol": "NESN.SW", "name": "Nestlé", "asset_type": "Action", "zone": "Europe", "sector": "Consommation"},
    {"symbol": "NOVN.SW", "name": "Novartis", "asset_type": "Action", "zone": "Europe", "sector": "Santé"},
    {"symbol": "ROG.SW", "name": "Roche", "asset_type": "Action", "zone": "Europe", "sector": "Santé"},
    {"symbol": "SHEL.L", "name": "Shell", "asset_type": "Action", "zone": "Europe", "sector": "Energie"},
    {"symbol": "AZN.L", "name": "AstraZeneca", "asset_type": "Action", "zone": "Europe", "sector": "Santé"},
    {"symbol": "HSBA.L", "name": "HSBC", "asset_type": "Action", "zone": "Europe", "sector": "Finance"},
    {"symbol": "IEUR", "name": "MSCI Europe ETF", "asset_type": "ETF", "zone": "Europe", "sector": "Indice Europe"},
    {"symbol": "EWQ", "name": "MSCI France ETF", "asset_type": "ETF", "zone": "Europe", "sector": "Indice France"},
    {"symbol": "VGK", "name": "FTSE Europe ETF", "asset_type": "ETF", "zone": "Europe", "sector": "Indice Europe"},
    {"symbol": "7203.T", "name": "Toyota", "asset_type": "Action", "zone": "Asie", "sector": "Automobile"},
    {"symbol": "6758.T", "name": "Sony Group", "asset_type": "Action", "zone": "Asie", "sector": "Technologie"},
    {"symbol": "9984.T", "name": "SoftBank Group", "asset_type": "Action", "zone": "Asie", "sector": "Télécoms"},
    {"symbol": "7974.T", "name": "Nintendo", "asset_type": "Action", "zone": "Asie", "sector": "Jeux vidéo"},
    {"symbol": "6861.T", "name": "Keyence", "asset_type": "Action", "zone": "Asie", "sector": "Industrie"},
    {"symbol": "8306.T", "name": "Mitsubishi UFJ", "asset_type": "Action", "zone": "Asie", "sector": "Finance"},
    {"symbol": "005930.KS", "name": "Samsung Electronics", "asset_type": "Action", "zone": "Asie", "sector": "Semi-conducteurs"},
    {"symbol": "000660.KS", "name": "SK Hynix", "asset_type": "Action", "zone": "Asie", "sector": "Semi-conducteurs"},
    {"symbol": "2330.TW", "name": "TSMC", "asset_type": "Action", "zone": "Asie", "sector": "Semi-conducteurs"},
    {"symbol": "2308.TW", "name": "Delta Electronics", "asset_type": "Action", "zone": "Asie", "sector": "Electronique"},
    {"symbol": "9988.HK", "name": "Alibaba HK", "asset_type": "Action", "zone": "Asie", "sector": "E-commerce"},
    {"symbol": "0700.HK", "name": "Tencent", "asset_type": "Action", "zone": "Asie", "sector": "Technologie"},
    {"symbol": "1211.HK", "name": "BYD", "asset_type": "Action", "zone": "Asie", "sector": "Automobile"},
    {"symbol": "3690.HK", "name": "Meituan", "asset_type": "Action", "zone": "Asie", "sector": "Internet"},
    {"symbol": "9618.HK", "name": "JD.com HK", "asset_type": "Action", "zone": "Asie", "sector": "E-commerce"},
    {"symbol": "2318.HK", "name": "Ping An", "asset_type": "Action", "zone": "Asie", "sector": "Finance"},
    {"symbol": "EWJ", "name": "MSCI Japan ETF", "asset_type": "ETF", "zone": "Asie", "sector": "Indice Japon"},
    {"symbol": "MCHI", "name": "MSCI China ETF", "asset_type": "ETF", "zone": "Asie", "sector": "Indice Chine"},
    {"symbol": "AAXJ", "name": "All Asia ex Japan ETF", "asset_type": "ETF", "zone": "Asie", "sector": "Indice Asie"},
    {"symbol": "EWT", "name": "MSCI Taiwan ETF", "asset_type": "ETF", "zone": "Asie", "sector": "Indice Taiwan"},
    {"symbol": "INFY", "name": "Infosys", "asset_type": "Action", "zone": "Pays émergent", "sector": "Technologie"},
    {"symbol": "VALE", "name": "Vale", "asset_type": "Action", "zone": "Pays émergent", "sector": "Matières premières"},
    {"symbol": "NIO", "name": "NIO", "asset_type": "Action", "zone": "Pays émergent", "sector": "Mobilité électrique"},
    {"symbol": "PBR", "name": "Petrobras", "asset_type": "Action", "zone": "Pays émergent", "sector": "Energie"},
    {"symbol": "ITUB", "name": "Itaú Unibanco", "asset_type": "Action", "zone": "Pays émergent", "sector": "Finance"},
    {"symbol": "BBD", "name": "Banco Bradesco", "asset_type": "Action", "zone": "Pays émergent", "sector": "Finance"},
    {"symbol": "MELI", "name": "MercadoLibre", "asset_type": "Action", "zone": "Pays émergent", "sector": "E-commerce"},
    {"symbol": "HDB", "name": "HDFC Bank", "asset_type": "Action", "zone": "Pays émergent", "sector": "Finance"},
    {"symbol": "IBN", "name": "ICICI Bank", "asset_type": "Action", "zone": "Pays émergent", "sector": "Finance"},
    {"symbol": "BIDU", "name": "Baidu", "asset_type": "Action", "zone": "Pays émergent", "sector": "Technologie"},
    {"symbol": "NTES", "name": "NetEase", "asset_type": "Action", "zone": "Pays émergent", "sector": "Jeux vidéo"},
    {"symbol": "BABA", "name": "Alibaba ADR", "asset_type": "Action", "zone": "Pays émergent", "sector": "E-commerce"},
    {"symbol": "JD", "name": "JD.com ADR", "asset_type": "Action", "zone": "Pays émergent", "sector": "E-commerce"},
    {"symbol": "EEM", "name": "MSCI Emerging Markets ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice EM"},
    {"symbol": "VWO", "name": "FTSE Emerging Markets ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice EM"},
    {"symbol": "EWZ", "name": "MSCI Brazil ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice Brésil"},
    {"symbol": "INDA", "name": "MSCI India ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice Inde"},
    {"symbol": "FXI", "name": "China Large-Cap ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice Chine"},
    {"symbol": "EZA", "name": "MSCI South Africa ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice Afrique du Sud"},
    {"symbol": "TUR", "name": "MSCI Turkey ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice Turquie"},
    {"symbol": "EWW", "name": "MSCI Mexico ETF", "asset_type": "ETF", "zone": "Pays émergent", "sector": "Indice Mexique"},
]

CATALOG_BY_SYMBOL = {row["symbol"]: row for row in ASSET_UNIVERSE}

LOGGER = logging.getLogger("portfolio_simulator")

DISPLAY_LABELS_FR = {
    "id": "ID",
    "created_at_utc": "Créé le (UTC)",
    "executed_at_utc": "Exécuté le (UTC)",
    "symbol": "Ticker",
    "name": "Nom",
    "asset_type": "Type d'actif",
    "sector": "Secteur",
    "zone": "Zone",
    "side": "Sens",
    "quantity": "Quantité",
    "price": "Prix",
    "fees": "Frais",
    "currency": "Devise",
    "fx_to_base": "FX vers devise portefeuille",
    "strategy": "Stratégie",
    "strategy_tag": "Tag stratégie",
    "exchange": "Place",
    "note": "Note",
    "order_type": "Type d'ordre",
    "trigger_price": "Prix de déclenchement",
    "execution_status": "Statut d'exécution",
    "fill_ratio": "Taux exécuté",
    "executed_quantity": "Quantité exécutée",
    "executed_price": "Prix exécuté",
    "slippage_bps": "Glissement (bps)",
    "spread_bps": "Écart achat/vente (bps)",
    "portfolio_value": "Valeur portefeuille",
    "cash": "Liquidités",
    "invested": "Investi",
    "pnl": "Gain/Perte",
    "pnl_pct": "Gain/Perte %",
    "pnl_latent": "Gain/Perte latent",
    "pnl_realise": "Gain/Perte réalisé",
    "pnl_total_live": "Gain/Perte total (temps réel)",
    "pnl_realise_historique": "Gain/Perte réalisé (historique)",
    "event_type": "Type d'événement",
    "event_label": "Libellé événement",
    "severity": "Sévérité",
    "title": "Titre",
    "message": "Message",
    "payload_json": "Payload JSON",
    "details_json": "Détails JSON",
    "delivered": "Envoyée",
    "provider": "Fournisseur",
    "score": "Score santé",
    "success": "Succès",
    "error": "Erreurs",
    "consecutive_error": "Erreurs consécutives",
    "circuit_open": "Circuit ouvert",
    "last_error": "Dernière erreur",
    "last_error_utc": "Horodatage erreur",
    "last": "Dernier",
    "previous": "Précédent",
    "change_pct": "Variation %",
    "quote_time_utc": "Horodatage cotation (UTC)",
    "market_state": "État marché",
    "source": "Source API",
    "regular_price": "Prix régulier",
    "pre_price": "Prix pré-marché",
    "post_price": "Prix post-marché",
    "official_close": "Clôture officielle",
    "price_context": "Contexte prix",
    "api_error": "Erreur API",
    "data_age_seconds": "Âge cotation (s)",
    "symbol_stale": "Actif obsolète",
    "source_health_score": "Santé fournisseur",
    "start_date": "Date début",
    "end_date": "Date fin",
    "initial_capital": "Capital initial",
    "initial_capital_backtest": "Capital initial simulation",
    "benchmark": "Indice de référence",
    "annual_return_pct": "Rendement annuel %",
    "volatility_pct": "Volatilité %",
    "sharpe": "Ratio de Sharpe",
    "max_drawdown_pct": "Repli maximal %",
    "final_value": "Valeur finale",
    "cum_fees_slippage": "Frais + glissement cumulés",
    "avg_turnover_pct": "Rotation moyenne %",
    "relative_vs_benchmark_pct": "Écart vs indice %",
    "action": "Action",
    "notional_base": "Montant devise portefeuille",
    "reason": "Raison",
    "priority": "Priorité",
    "ret_1m": "Perf 1 mois %",
    "ret_3m": "Perf 3 mois %",
    "vol_3m": "Volatilité 3 mois %",
    "size_pct": "Poids cible %",
    "confidence": "Confiance",
    "rationale": "Justification",
    "risks": "Risques",
    "invalidation": "Invalidation",
    "date": "Date",
    "equity": "Capital",
    "returns": "Rendement",
    "drawdown": "Repli maximal",
    "turnover": "Rotation",
    "cost_value": "Coût total",
    "benchmark_equity": "Capital indice",
}

DISPLAY_VALUES_FR = {
    "BUY": "Achat",
    "SELL": "Vente",
    "MARKET": "Marché",
    "LIMIT": "Limite",
    "STOP": "Stop",
    "FILLED": "Exécuté",
    "PARTIAL": "Partiel",
    "PENDING": "En attente",
    "INIT": "Initialisation",
    "UP": "Hausse",
    "DOWN": "Baisse",
    "OPEN": "Ouvert",
    "CLOSED": "Fermé",
    "UNKNOWN": "Inconnu",
    "DELAYED": "Différé",
    "UNAVAILABLE": "Indisponible",
    "STREAMING": "Flux continu",
    "UNAUTHENTICATED": "Non authentifié",
    "disabled": "désactivé",
    "n/a": "n/d",
    "starting": "démarrage",
    "connected": "connecté",
    "authenticating": "authentification",
    "reconnecting": "reconnexion",
    "stopped": "arrêté",
    "idle": "veille",
    "error": "erreur",
    "regular": "séance",
    "pre": "pré-marché",
    "post": "post-marché",
    "off_session": "hors séance",
    "bid_fallback": "secours bid",
    "delayed": "différé",
    "tick": "tick",
    "unavailable": "indisponible",
    "buy_hold": "Achat & conservation",
    "sma50": "SMA50 dynamique",
    "True": "Oui",
    "False": "Non",
}


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


def localize_value_fr(value: object) -> object:
    if isinstance(value, (bool, np.bool_)):
        return "Oui" if bool(value) else "Non"
    if value is None:
        return value
    if isinstance(value, (int, float, np.integer, np.floating)) and not pd.isna(value):
        return value
    raw = str(value).strip()
    if not raw:
        return value
    for key in (raw, raw.upper(), raw.lower()):
        if key in DISPLAY_VALUES_FR:
            return DISPLAY_VALUES_FR[key]
    return value


def localize_text_fr(text: object) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for key in (raw, raw.upper(), raw.lower()):
        if key in DISPLAY_VALUES_FR:
            return DISPLAY_VALUES_FR[key]
    return raw


def localize_dataframe_fr(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if df.empty:
        renamed = {c: DISPLAY_LABELS_FR.get(str(c), str(c)) for c in df.columns}
        return df.rename(columns=renamed)
    out = df.copy()
    out = out.rename(columns={c: DISPLAY_LABELS_FR.get(str(c), str(c)) for c in out.columns})
    for col in out.columns:
        if pd.api.types.is_bool_dtype(out[col]):
            out[col] = out[col].map(lambda v: "Oui" if bool(v) else "Non")
        elif pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(localize_value_fr)
    return out


def render_dataframe_fr(data: pd.DataFrame, **kwargs) -> None:
    localized = localize_dataframe_fr(data)
    try:
        styled = localized.style.set_table_styles(
            [
                {"selector": "table", "props": [("background-color", "#ffffff"), ("color", "#102a5c")]},
                {"selector": "th", "props": [("background-color", "#edf4ff"), ("color", "#102a5c"), ("font-weight", "700")]},
                {"selector": "td", "props": [("background-color", "#ffffff"), ("color", "#102a5c")]},
            ],
            overwrite=False,
        ).set_properties(
            **{
                "background-color": "#ffffff",
                "color": "#102a5c",
                "border-color": "#d7e3f8",
            }
        )
        st.dataframe(styled, **kwargs)
    except Exception:
        st.dataframe(localized, **kwargs)


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
        "auto_refresh_enabled": "0",
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
        "benchmark_symbol": DEFAULT_BENCHMARK_SYMBOL,
        "trade_slippage_bps": str(DEFAULT_SIM_SLIPPAGE_BPS),
        "trade_spread_bps": str(DEFAULT_SIM_SPREAD_BPS),
        "backtest_fees_bps": "8.0",
        "backtest_slippage_bps": "5.0",
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


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("utf-8"))


def jwt_decode_hs256(token: str, secret: str) -> dict:
    if not token or len(token) > JWT_MAX_TOKEN_LENGTH:
        raise ValueError("Invalid token length")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    header_b64, payload_b64, sig_b64 = parts
    header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("Invalid JWT header")
    if str(header.get("alg", "")).upper() != "HS256":
        raise ValueError("Unsupported JWT algorithm")
    typ = str(header.get("typ", "JWT")).upper()
    if typ and typ != "JWT":
        raise ValueError("Unsupported JWT type")
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature = _b64url_decode(sig_b64)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid signature")
    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid JWT payload")
    now = int(datetime.now(tz=timezone.utc).timestamp())
    try:
        leeway = int(os.getenv("PORTFOLIO_AUTH_LEEWAY_SECONDS", str(JWT_DEFAULT_LEEWAY_SECONDS)))
    except Exception:
        leeway = JWT_DEFAULT_LEEWAY_SECONDS
    leeway = max(0, min(leeway, 300))
    exp = payload.get("exp")
    if exp is None:
        raise ValueError("Missing exp claim")
    if int(exp) + leeway < now:
        raise ValueError("Token expired")
    nbf = payload.get("nbf")
    if nbf is not None and int(nbf) > now + leeway:
        raise ValueError("Token not active yet")
    iat = payload.get("iat")
    if iat is not None and int(iat) > now + leeway:
        raise ValueError("Token issued in the future")
    expected_iss = os.getenv("PORTFOLIO_AUTH_ISS", "").strip()
    if expected_iss and str(payload.get("iss", "")).strip() != expected_iss:
        raise ValueError("Invalid issuer")
    expected_aud = os.getenv("PORTFOLIO_AUTH_AUD", "").strip()
    if expected_aud:
        aud = payload.get("aud")
        if isinstance(aud, list):
            if expected_aud not in [str(v) for v in aud]:
                raise ValueError("Invalid audience")
        elif str(aud) != expected_aud:
            raise ValueError("Invalid audience")
    return payload


def get_auth_mode() -> str:
    mode = os.getenv("PORTFOLIO_AUTH_MODE", DEFAULT_AUTH_MODE).strip().lower()
    return mode if mode in {"optional", "required", "off"} else DEFAULT_AUTH_MODE


def get_base44_auth_payload() -> dict | None:
    mode = get_auth_mode()
    if mode == "off":
        return None
    token_raw = st.query_params.get("token")
    token = ""
    if isinstance(token_raw, list):
        token = str(token_raw[0]) if token_raw else ""
    elif token_raw is not None:
        token = str(token_raw)
    token = token.strip()
    if not token:
        if mode == "required":
            st.error("🔒 Accès refusé: token manquant.")
            st.stop()
        return None
    try:
        secret = str(st.secrets["SHARED_SECRET"]).strip()
    except Exception:
        st.error("🔧 Configuration manquante: SHARED_SECRET")
        st.stop()
    if not secret:
        st.error("🔧 SHARED_SECRET vide.")
        st.stop()
    try:
        payload = jwt_decode_hs256(str(token), secret)
    except Exception:
        st.error("🔒 Token invalide ou expiré.")
        st.stop()
    email = str(payload.get("email", "")).strip().lower()
    if not email:
        st.error("🔒 Token invalide: email manquant.")
        st.stop()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        st.error("🔒 Token invalide: email mal formé.")
        st.stop()
    payload["email"] = email
    return payload


def user_db_path(user_email: str) -> Path:
    user_root = Path("data/users")
    user_root.mkdir(parents=True, exist_ok=True)
    safe_user = hashlib.sha256(user_email.strip().lower().encode("utf-8")).hexdigest()[:24]
    return user_root / f"{safe_user}.db"


def _provider_health(provider: str) -> dict[str, float | str]:
    with PROVIDER_HEALTH_LOCK:
        return dict(PROVIDER_HEALTH.get(provider, {}))


def provider_health_score(provider: str) -> float:
    h = _provider_health(provider)
    success = float(h.get("success", 0.0))
    error = float(h.get("error", 0.0))
    consecutive = float(h.get("consecutive_error", 0.0))
    open_until = float(h.get("circuit_open_until", 0.0))
    total = success + error
    base = (success / total * 100.0) if total > 0 else 75.0
    penalty = min(consecutive * 8.0, 35.0)
    if open_until > time.monotonic():
        penalty += 30.0
    return float(max(0.0, min(100.0, base - penalty)))


def provider_health_table() -> pd.DataFrame:
    rows = []
    now_mono = time.monotonic()
    with PROVIDER_HEALTH_LOCK:
        for provider, health in PROVIDER_HEALTH.items():
            success = float(health.get("success", 0.0))
            error = float(health.get("error", 0.0))
            consecutive = float(health.get("consecutive_error", 0.0))
            open_until = float(health.get("circuit_open_until", 0.0))
            total = success + error
            base = (success / total * 100.0) if total > 0 else 75.0
            penalty = min(consecutive * 8.0, 35.0)
            if open_until > now_mono:
                penalty += 30.0
            score = float(max(0.0, min(100.0, base - penalty)))
            rows.append(
                {
                    "provider": provider,
                    "score": score,
                    "success": int(success),
                    "error": int(error),
                    "consecutive_error": int(consecutive),
                    "circuit_open": open_until > now_mono,
                    "last_error": str(health.get("last_error", "")),
                    "last_error_utc": str(health.get("last_error_utc", "")),
                }
            )
    return pd.DataFrame(rows)


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
            if attempt < API_MAX_RETRIES - 1:
                sleep_for = API_BACKOFF_BASE_SECONDS * (2**attempt)
                time.sleep(sleep_for)
    return None, last_error or "network_error"


def deterministic_fill_ratio(symbol: str, order_type: str, quantity: float) -> float:
    o = order_type.upper()
    low = DEFAULT_SIM_PARTIAL_MIN if o in {"LIMIT", "STOP"} else 0.75
    high = DEFAULT_SIM_PARTIAL_MAX
    key = f"{symbol}|{o}|{quantity:.6f}|{utc_now_iso()[:16]}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    rnd = int.from_bytes(digest[:8], "big") / float(2**64)
    return float(low + (high - low) * rnd)


def simulate_order_execution(
    side: str,
    order_type: str,
    market_price: float,
    quantity: float,
    trigger_price: float | None,
    slippage_bps: float,
    spread_bps: float,
    symbol: str,
) -> dict[str, float | str]:
    side = side.upper()
    otype = order_type.upper()
    market_price = float(market_price)
    quantity = float(quantity)
    spread_factor = spread_bps / 20_000.0
    slip_factor = slippage_bps / 10_000.0
    if side == "BUY":
        impacted_price = market_price * (1.0 + spread_factor + slip_factor)
    else:
        impacted_price = market_price * (1.0 - spread_factor - slip_factor)
    triggered = True
    if otype == "LIMIT":
        if trigger_price is None or trigger_price <= 0:
            triggered = False
        elif side == "BUY":
            triggered = market_price <= float(trigger_price)
        else:
            triggered = market_price >= float(trigger_price)
    elif otype == "STOP":
        if trigger_price is None or trigger_price <= 0:
            triggered = False
        elif side == "BUY":
            triggered = market_price >= float(trigger_price)
        else:
            triggered = market_price <= float(trigger_price)
    if not triggered:
        return {
            "execution_status": "PENDING",
            "fill_ratio": 0.0,
            "executed_quantity": 0.0,
            "executed_price": market_price,
            "order_message": "Ordre non déclenché selon la condition de prix.",
        }
    ratio = deterministic_fill_ratio(symbol=symbol, order_type=otype, quantity=quantity)
    executed_qty = float(max(0.0, min(quantity, quantity * ratio)))
    status = "FILLED" if abs(executed_qty - quantity) <= 1e-9 else "PARTIAL"
    return {
        "execution_status": status,
        "fill_ratio": float(executed_qty / quantity) if quantity > 0 else 0.0,
        "executed_quantity": executed_qty,
        "executed_price": impacted_price,
        "order_message": f"Ordre {status.lower()} ({executed_qty:.4f}/{quantity:.4f})",
    }


def annotate_quote_freshness(quotes: pd.DataFrame, stale_seconds: int) -> pd.DataFrame:
    if quotes is None or quotes.empty:
        return quotes
    out = quotes.copy()
    out["quote_ts"] = pd.to_datetime(out["quote_time_utc"], errors="coerce", utc=True)
    now_ts = pd.Timestamp.now(tz="UTC")
    out["data_age_seconds"] = (now_ts - out["quote_ts"]).dt.total_seconds().fillna(1e9).clip(lower=0).round(1)
    out["symbol_stale"] = out["data_age_seconds"] > float(max(stale_seconds, 1))
    out["source_health_score"] = out["source"].map(lambda s: round(provider_health_score(str(s)), 1))
    return out.drop(columns=["quote_ts"])


def quote_freshness_summary(quotes: pd.DataFrame) -> str:
    if quotes is None or quotes.empty or "data_age_seconds" not in quotes.columns:
        return "Fraîcheur des données: indisponible."
    ages = pd.to_numeric(quotes["data_age_seconds"], errors="coerce").dropna()
    if ages.empty:
        return "Fraîcheur des données: indisponible."
    return f"Fraîcheur: min {ages.min():.1f}s | médiane {ages.median():.1f}s | max {ages.max():.1f}s"


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
                "api_error",
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
                    "api_error": "",
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
def get_connection() -> sqlite3.Connection:
    return get_connection_for_path(str(DB_PATH))


@st.cache_resource
def get_connection_for_path(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


@st.cache_resource
def get_connection_for_user(user_email: str) -> sqlite3.Connection:
    return get_connection_for_path(str(user_db_path(user_email)))


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
            note TEXT,
            order_type TEXT DEFAULT 'MARKET',
            trigger_price REAL,
            execution_status TEXT DEFAULT 'FILLED',
            fill_ratio REAL DEFAULT 1.0,
            executed_quantity REAL,
            executed_price REAL,
            slippage_bps REAL DEFAULT 0.0,
            spread_bps REAL DEFAULT 0.0
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
    ensure_column_exists(conn, "transactions", "order_type", "TEXT DEFAULT 'MARKET'")
    ensure_column_exists(conn, "transactions", "trigger_price", "REAL")
    ensure_column_exists(conn, "transactions", "execution_status", "TEXT DEFAULT 'FILLED'")
    ensure_column_exists(conn, "transactions", "fill_ratio", "REAL DEFAULT 1.0")
    ensure_column_exists(conn, "transactions", "executed_quantity", "REAL")
    ensure_column_exists(conn, "transactions", "executed_price", "REAL")
    ensure_column_exists(conn, "transactions", "slippage_bps", "REAL DEFAULT 0.0")
    ensure_column_exists(conn, "transactions", "spread_bps", "REAL DEFAULT 0.0")
    ensure_column_exists(conn, "backtest_runs", "curve_json", "TEXT")
    ensure_column_exists(conn, "backtest_runs", "benchmark", "TEXT")
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
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('auto_refresh_enabled', '0')"
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
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('benchmark_symbol', ?)",
        (DEFAULT_BENCHMARK_SYMBOL,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('trade_slippage_bps', ?)",
        (str(DEFAULT_SIM_SLIPPAGE_BPS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('trade_spread_bps', ?)",
        (str(DEFAULT_SIM_SPREAD_BPS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('backtest_fees_bps', '8.0')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('backtest_slippage_bps', '5.0')"
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
        SELECT
            id,
            executed_at_utc,
            symbol,
            side,
            quantity,
            price,
            fees,
            currency,
            fx_to_base,
            strategy_tag,
            exchange,
            note,
            order_type,
            trigger_price,
            execution_status,
            fill_ratio,
            executed_quantity,
            executed_price,
            slippage_bps,
            spread_bps
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
                "order_type",
                "trigger_price",
                "execution_status",
                "fill_ratio",
                "executed_quantity",
                "executed_price",
                "slippage_bps",
                "spread_bps",
            ]
        )
    df["symbol"] = df["symbol"].str.upper()
    df["currency"] = df["currency"].fillna(DEFAULT_BASE_CURRENCY).str.upper()
    df["fx_to_base"] = pd.to_numeric(df["fx_to_base"], errors="coerce").fillna(1.0)
    df["order_type"] = df["order_type"].fillna("MARKET").str.upper()
    df["execution_status"] = df["execution_status"].fillna("FILLED").str.upper()
    df["executed_quantity"] = pd.to_numeric(df["executed_quantity"], errors="coerce")
    df["executed_price"] = pd.to_numeric(df["executed_price"], errors="coerce")
    df["trigger_price"] = pd.to_numeric(df["trigger_price"], errors="coerce")
    df["fill_ratio"] = pd.to_numeric(df["fill_ratio"], errors="coerce").fillna(1.0)
    df["slippage_bps"] = pd.to_numeric(df["slippage_bps"], errors="coerce").fillna(0.0)
    df["spread_bps"] = pd.to_numeric(df["spread_bps"], errors="coerce").fillna(0.0)
    df["executed_quantity"] = df["executed_quantity"].fillna(pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0))
    df["executed_price"] = df["executed_price"].fillna(pd.to_numeric(df["price"], errors="coerce").fillna(0.0))
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
    order_type: str = "MARKET",
    trigger_price: float | None = None,
    execution_status: str = "FILLED",
    fill_ratio: float = 1.0,
    executed_quantity: float | None = None,
    executed_price: float | None = None,
    slippage_bps: float = 0.0,
    spread_bps: float = 0.0,
) -> None:
    executed_quantity = quantity if executed_quantity is None else executed_quantity
    executed_price = price if executed_price is None else executed_price
    conn.execute(
        """
        INSERT INTO transactions(
            executed_at_utc, symbol, side, quantity, price, fees, currency, fx_to_base,
            strategy_tag, exchange, note, order_type, trigger_price, execution_status,
            fill_ratio, executed_quantity, executed_price, slippage_bps, spread_bps
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            order_type.upper(),
            (float(trigger_price) if trigger_price is not None and trigger_price > 0 else None),
            execution_status.upper(),
            float(fill_ratio),
            float(executed_quantity),
            float(executed_price),
            float(slippage_bps),
            float(spread_bps),
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
        status = str(getattr(tx, "execution_status", "FILLED")).upper()
        if status not in {"FILLED", "PARTIAL"}:
            continue
        exec_qty = safe_float(getattr(tx, "executed_quantity", getattr(tx, "quantity", 0.0)), 0.0)
        exec_price = safe_float(getattr(tx, "executed_price", getattr(tx, "price", 0.0)), 0.0)
        if exec_qty <= 0 or exec_price <= 0:
            continue
        notional = float(exec_qty) * float(exec_price)
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
        status = str(getattr(tx, "execution_status", "FILLED")).upper()
        if status not in {"FILLED", "PARTIAL"}:
            continue
        qty = safe_float(getattr(tx, "executed_quantity", tx.quantity), 0.0)
        price = safe_float(getattr(tx, "executed_price", tx.price), 0.0)
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
        "api_error",
    ]
    cleaned = tuple(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()]))
    if not cleaned:
        return pd.DataFrame(columns=columns)

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
                "api_error": "",
            }
            if symbol in symbol_errors:
                symbol_errors.pop(symbol, None)

    missing = [s for s in cleaned if s not in rows_by_symbol]
    if missing:
        fallback = fetch_quotes(tuple(missing))
        if not fallback.empty:
            for row in fallback.itertuples(index=False):
                sym = str(row.symbol).upper()
                rows_by_symbol[sym] = {
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
                    "api_error": symbol_errors.get(sym, ""),
                }
                _provider_record_success("yfinance_history")
                symbol_errors.pop(sym, None)

    unresolved = [s for s in cleaned if s not in rows_by_symbol]
    for sym in unresolved:
        rows_by_symbol[sym] = {
            "symbol": sym,
            "last": np.nan,
            "previous": np.nan,
            "change_pct": np.nan,
            "quote_time_utc": utc_now_iso(),
            "market_state": "UNAVAILABLE",
            "currency": "",
            "source": "unavailable",
            "regular_price": np.nan,
            "pre_price": np.nan,
            "post_price": np.nan,
            "official_close": np.nan,
            "price_context": "unavailable",
            "api_error": symbol_errors.get(sym, "quote_unavailable"),
        }

    if not rows_by_symbol:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(rows_by_symbol.values()).sort_values("symbol").reset_index(drop=True)
    return out


@st.cache_data(ttl=3, show_spinner=False)
def fetch_polygon_snapshot_quotes(symbols: tuple[str, ...], api_key: str) -> pd.DataFrame:
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
        "api_error",
    ]
    token = str(api_key or "").strip()
    supported = tuple(sorted(set([s.strip().upper() for s in symbols if s.strip() and polygon_symbol_supported(s)])))
    if not token or not supported:
        return pd.DataFrame(columns=columns)

    rows_by_symbol: dict[str, dict] = {}
    symbol_errors: dict[str, str] = {}
    for part in chunked(list(supported), 50):
        joined = ",".join(part)
        url = (
            "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
            f"?tickers={urllib.parse.quote(joined)}&apiKey={urllib.parse.quote(token)}"
        )
        payload, err = _http_get_json_with_resilience(url, provider="polygon_ws_tick", timeout=8)
        if err:
            for s in part:
                symbol_errors[s] = f"polygon_snapshot:{err}"
            continue

        tickers = (payload or {}).get("tickers", [])
        for item in tickers:
            symbol = str(item.get("ticker", "")).upper()
            if not symbol:
                continue
            last_trade = item.get("lastTrade") or {}
            day = item.get("day") or {}
            prev_day = item.get("prevDay") or {}
            last = safe_float(last_trade.get("p"), np.nan)
            if np.isnan(last) or last <= 0:
                last = safe_float(day.get("c"), np.nan)
            previous = safe_float(prev_day.get("c"), np.nan)
            if np.isnan(previous) or previous <= 0:
                previous = safe_float(day.get("o"), last)
            if np.isnan(last) or last <= 0:
                continue
            if np.isnan(previous) or previous <= 0:
                previous = float(last)
            raw_change = item.get("todaysChangePerc")
            if raw_change is None:
                raw_change = ((last / previous) - 1.0) * 100 if previous else 0.0
            ts_iso = any_epoch_to_iso(last_trade.get("t")) if last_trade.get("t") else utc_now_iso()
            rows_by_symbol[symbol] = {
                "symbol": symbol,
                "last": float(last),
                "previous": float(previous),
                "change_pct": float(raw_change),
                "quote_time_utc": ts_iso,
                "market_state": "REGULAR",
                "currency": "USD",
                "source": "polygon_ws_tick",
                "regular_price": float(last),
                "pre_price": 0.0,
                "post_price": 0.0,
                "official_close": float(previous),
                "price_context": "snapshot",
                "api_error": "",
            }
            symbol_errors.pop(symbol, None)

    unresolved = [s for s in supported if s not in rows_by_symbol]
    for sym in unresolved:
        rows_by_symbol[sym] = {
            "symbol": sym,
            "last": np.nan,
            "previous": np.nan,
            "change_pct": np.nan,
            "quote_time_utc": utc_now_iso(),
            "market_state": "UNAVAILABLE",
            "currency": "USD",
            "source": "polygon_ws_tick",
            "regular_price": np.nan,
            "pre_price": np.nan,
            "post_price": np.nan,
            "official_close": np.nan,
            "price_context": "unavailable",
            "api_error": symbol_errors.get(sym, "polygon_unavailable"),
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


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_split_factors(symbols: tuple[str, ...], lookback_years: int = 10) -> dict[str, float]:
    yf = _safe_yf_import()
    factors: dict[str, float] = {}
    if not yf:
        return factors
    start = (pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=max(1, int(lookback_years)))).date().isoformat()
    for symbol in symbols:
        try:
            hist = yf.Ticker(symbol).history(start=start, interval="1d", auto_adjust=False, actions=True)
            if "Stock Splits" not in hist.columns or hist.empty:
                factors[symbol] = 1.0
                continue
            splits = pd.to_numeric(hist["Stock Splits"], errors="coerce").dropna()
            splits = splits[splits > 0]
            if splits.empty:
                factors[symbol] = 1.0
            else:
                factors[symbol] = float(splits.prod())
        except Exception:
            factors[symbol] = 1.0
    return factors


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_trailing_dividends_per_share(symbols: tuple[str, ...]) -> dict[str, float]:
    yf = _safe_yf_import()
    out: dict[str, float] = {}
    if not yf:
        return out
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)
    for symbol in symbols:
        try:
            div = yf.Ticker(symbol).dividends
            if div is None or len(div) == 0:
                out[symbol] = 0.0
                continue
            idx = pd.to_datetime(div.index, errors="coerce")
            if getattr(idx, "tz", None) is None:
                idx = idx.tz_localize("UTC")
            else:
                idx = idx.tz_convert("UTC")
            s = pd.Series(div.values, index=idx).sort_index()
            out[symbol] = float(pd.to_numeric(s[s.index >= cutoff], errors="coerce").fillna(0.0).sum())
        except Exception:
            out[symbol] = 0.0
    return out


def apply_split_adjustments_to_positions(
    positions: pd.DataFrame,
    split_factors: dict[str, float],
) -> pd.DataFrame:
    if positions.empty:
        return positions
    out = positions.copy()
    out["split_factor"] = out["symbol"].map(lambda s: safe_float(split_factors.get(str(s), 1.0), 1.0)).fillna(1.0)
    out["split_factor"] = out["split_factor"].replace(0, 1.0)
    mask = out["split_factor"] > 0
    out.loc[mask, "quantity"] = out.loc[mask, "quantity"] * out.loc[mask, "split_factor"]
    out.loc[mask, "avg_cost"] = out.loc[mask, "avg_cost"] / out.loc[mask, "split_factor"]
    out.loc[mask, "book_value"] = out.loc[mask, "quantity"] * out.loc[mask, "avg_cost"]
    return out.drop(columns=["split_factor"])


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
    trailing_dividends_per_share: dict[str, float] | None = None,
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
        realized_quote = safe_float(getattr(pos, "realized_pnl", 0.0), 0.0)
        realized_base_hist = safe_float(getattr(pos, "realized_pnl_base", np.nan), np.nan)
        if np.isnan(realized_base_hist):
            realized_base_hist = realized_quote * avg_fx_to_base
        realized_base_live = realized_quote * fx_to_base
        pnl_total_live = unrealized + realized_base_live
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
                "pnl_realise": float(realized_base_live),
                "pnl_realise_historique": float(realized_base_hist),
                "pnl_total_live": float(pnl_total_live),
                "dividend_yield": float(profile.get("dividend_yield", 0.0)),
                "trailing_div_ps": safe_float((trailing_dividends_per_share or {}).get(pos.symbol, 0.0), 0.0),
                "avg_fx_to_base": avg_fx_to_base,
            }
        )

    holdings = pd.DataFrame(holdings_rows)
    if holdings.empty:
        invested = 0.0
        annual_dividends = 0.0
    else:
        invested = float(holdings["valeur_marche"].sum())
        implied = pd.to_numeric(holdings["valeur_marche"] * holdings["dividend_yield"], errors="coerce").fillna(0.0)
        trailing = pd.to_numeric(
            holdings["quantite"] * holdings["trailing_div_ps"] * holdings["fx_to_base"], errors="coerce"
        ).fillna(0.0)
        annual_dividends = float(pd.concat([implied, trailing], axis=1).max(axis=1).sum())

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
    actions.append("2) Exécuter par paliers et journaliser chaque achat/vente pour conserver la cohérence des instantanés.")
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
            --navy-950:#041022;
            --navy-900:#071733;
            --navy-850:#0b2454;
            --navy-800:#0f2f79;
            --navy-700:#1a4aa5;
            --surface:#ffffff;
            --surface-soft:#f3f7ff;
            --line-soft:#cfdcf2;
            --text-main:#102a5c;
            --text-muted:#4f6388;
            --gain:#0f9d58;
            --loss:#d93025;
            --card-radius:20px;
        }
        div[data-testid="stStatusWidget"] {
            display: none !important;
        }
        div[data-testid="stSpinner"],
        div[data-testid="stLoadingSpinner"],
        div[data-testid="stAppSkeleton"] {
            display: none !important;
        }
        .stale-element {
            opacity: 1 !important;
            filter: none !important;
        }
        [data-stale="true"] {
            opacity: 1 !important;
            filter: none !important;
        }
        .stApp {
            background:
                radial-gradient(circle at 12% 8%, rgba(17, 73, 170, 0.08), transparent 36%),
                linear-gradient(180deg, #f9fbff 0%, #edf3ff 100%);
            color: var(--text-main);
        }
        header[data-testid="stHeader"] {
            background: linear-gradient(180deg, var(--navy-900) 0%, var(--navy-950) 100%);
            border-bottom: 1px solid #123a82;
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #030f25 0%, #06173a 100%);
            border-right: 1px solid rgba(148, 163, 184, 0.25);
        }
        section[data-testid="stSidebar"] * {
            color: #e8eefb !important;
        }
        section[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] {
            color: #d8e2fb !important;
            background: transparent !important;
        }
        section[data-testid="stSidebar"] .stCaption {
            color: #b8c6e6 !important;
        }
        section[data-testid="stSidebar"] .stNumberInput input,
        section[data-testid="stSidebar"] .stTextInput input,
        section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"],
        section[data-testid="stSidebar"] .stTextArea textarea,
        section[data-testid="stSidebar"] [data-baseweb="tag"] {
            background: #0d1f47 !important;
            border-color: #1f3c78 !important;
            color: #f7faff !important;
        }
        section[data-testid="stSidebar"] [data-baseweb="tag"] span {
            color: #f7faff !important;
        }
        section[data-testid="stSidebar"] .stButton > button {
            background: linear-gradient(160deg, #1d3f95 0%, #1a53c2 100%) !important;
            border: 1px solid #2f65d2 !important;
            color: #f7faff !important;
        }
        section[data-testid="stSidebar"] .stButton > button:hover {
            filter: brightness(1.05);
        }
        section[data-testid="stSidebar"] [data-baseweb="slider"] div[role="slider"] {
            background: #2a66d4 !important;
            border-color: #2a66d4 !important;
        }
        div[data-testid="stAppViewContainer"] .stNumberInput input,
        div[data-testid="stAppViewContainer"] .stTextInput input,
        div[data-testid="stAppViewContainer"] .stTextArea textarea,
        div[data-testid="stAppViewContainer"] .stDateInput input,
        div[data-testid="stAppViewContainer"] .stSelectbox div[data-baseweb="select"] {
            background: #ffffff !important;
            border: 1px solid #c6d7f3 !important;
            color: #102a5c !important;
        }
        div[data-testid="stAppViewContainer"] [data-baseweb="tag"] {
            background: #eaf2ff !important;
            border: 1px solid #bfd3f5 !important;
            color: #14386f !important;
        }
        div[data-testid="stAppViewContainer"] [data-baseweb="tag"] span {
            color: #14386f !important;
        }
        div[data-testid="stAppViewContainer"] .stButton > button {
            background: linear-gradient(160deg, #0f2f79 0%, #15479b 100%) !important;
            border: 1px solid #1f55b1 !important;
            color: #ffffff !important;
        }
        div[data-testid="stAppViewContainer"] .stButton > button:hover {
            filter: brightness(1.06);
        }
        div[data-testid="stAppViewContainer"] [data-baseweb="slider"] div[role="slider"] {
            background: #0f2f79 !important;
            border-color: #0f2f79 !important;
        }
        div[data-testid="stAppViewContainer"] .stExpander {
            background: #ffffff;
            border: 1px solid #ccdbf3;
            border-radius: 12px;
        }
        div[data-testid="stAppViewContainer"] .stExpander summary {
            color: #102a5c !important;
            font-weight: 700;
        }
        div[data-testid="stAppViewContainer"] .block-container,
        div[data-testid="stAppViewContainer"] .block-container h1,
        div[data-testid="stAppViewContainer"] .block-container h2,
        div[data-testid="stAppViewContainer"] .block-container h3,
        div[data-testid="stAppViewContainer"] .block-container h4,
        div[data-testid="stAppViewContainer"] .block-container h5,
        div[data-testid="stAppViewContainer"] .block-container h6,
        div[data-testid="stAppViewContainer"] .block-container p,
        div[data-testid="stAppViewContainer"] .block-container li,
        div[data-testid="stAppViewContainer"] .block-container strong,
        div[data-testid="stAppViewContainer"] .block-container em,
        div[data-testid="stAppViewContainer"] .block-container label,
        div[data-testid="stAppViewContainer"] .block-container small,
        div[data-testid="stAppViewContainer"] .block-container code {
            color: #102a5c !important;
        }
        .stApp {
            --gdg-bg-cell: #ffffff;
            --gdg-bg-cell-medium: #f7faff;
            --gdg-bg-header: #edf4ff;
            --gdg-bg-header-has-focus: #e7f0ff;
            --gdg-bg-header-hovered: #e7f0ff;
            --gdg-bg-row-hover: #eef4ff;
            --gdg-bg-odd: #f8fbff;
            --gdg-text-dark: #102a5c;
            --gdg-text-medium: #2d4778;
            --gdg-text-light: #5c739d;
            --gdg-text-header: #102a5c;
            --gdg-text-header-selected: #102a5c;
            --gdg-bg-cell-selected: #e7f0ff;
            --gdg-accent-color: #0f2f79;
            --gdg-horizontal-border-color: #d7e3f8;
            --gdg-vertical-border-color: #d7e3f8;
            --gdg-border-color: #d7e3f8;
            --gdg-header-font-style: 700 13px "Source Sans Pro", sans-serif;
        }
        button[data-baseweb="tab"] {
            color: #213d71 !important;
        }
        div[data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: .25rem;
            border-bottom: 1px solid #c7d5ef;
            padding-bottom: .15rem;
        }
        div[data-testid="stTabs"] [data-baseweb="tab"] {
            background: #eef4ff;
            border: 1px solid #cfddf6;
            border-radius: 10px 10px 0 0;
            padding: .35rem .85rem;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            color: var(--navy-850) !important;
            background: #ffffff !important;
            border: 1px solid #b9cff1 !important;
            border-bottom: 2px solid #d93025 !important;
        }
        div[data-testid="stDataFrame"] {
            background: var(--surface) !important;
            border: 1px solid var(--line-soft);
            border-radius: 14px;
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stDataFrame"] > div,
        div[data-testid="stDataFrame"] [data-testid="stDataFrameResizable"] {
            --gdg-bg-cell: #ffffff !important;
            --gdg-bg-cell-medium: #f7faff !important;
            --gdg-bg-header: #edf4ff !important;
            --gdg-bg-header-has-focus: #e7f0ff !important;
            --gdg-bg-header-hovered: #e7f0ff !important;
            --gdg-bg-row-hover: #eef4ff !important;
            --gdg-bg-odd: #f8fbff !important;
            --gdg-bg-cell-selected: #e7f0ff !important;
            --gdg-accent-color: #0f2f79 !important;
            --gdg-text-dark: #102a5c !important;
            --gdg-text-medium: #2d4778 !important;
            --gdg-text-light: #5c739d !important;
            --gdg-text-header: #102a5c !important;
            --gdg-text-header-selected: #102a5c !important;
            --gdg-horizontal-border-color: #d7e3f8 !important;
            --gdg-vertical-border-color: #d7e3f8 !important;
            --gdg-border-color: #d7e3f8 !important;
        }
        div[data-testid="stDataFrame"] * {
            color: var(--text-main) !important;
        }
        div[data-testid="stDataFrame"] canvas {
            background: #ffffff !important;
        }
        div[data-testid="stDataFrame"] [class*="glide"],
        div[data-testid="stDataFrame"] [class*="glide-"],
        div[data-testid="stDataFrame"] [class*="gdg"] {
            background: #ffffff !important;
            color: #102a5c !important;
        }
        div[data-testid="stDataFrame"] [class*="header"] {
            background: #edf4ff !important;
            color: #102a5c !important;
        }
        div[data-testid="stMarkdownContainer"] table {
            background: #ffffff !important;
            color: #102a5c !important;
            border-collapse: collapse !important;
            width: 100%;
        }
        div[data-testid="stMarkdownContainer"] table th {
            background: #edf4ff !important;
            color: #102a5c !important;
            border: 1px solid #d7e3f8 !important;
            font-weight: 700 !important;
        }
        div[data-testid="stMarkdownContainer"] table td {
            background: #ffffff !important;
            color: #102a5c !important;
            border: 1px solid #d7e3f8 !important;
        }
        div[data-testid="stAppViewContainer"] [role="listbox"] {
            background: #ffffff !important;
            color: #102a5c !important;
            border: 1px solid #c6d7f3 !important;
        }
        div[data-testid="stAppViewContainer"] [role="option"] {
            background: #ffffff !important;
            color: #102a5c !important;
        }
        div[data-testid="stAppViewContainer"] [role="option"][aria-selected="true"] {
            background: #eaf2ff !important;
            color: #0f2f79 !important;
        }
        div[data-testid="stPlotlyChart"] {
            background: #ffffff !important;
            border: 1px solid #d7e3f8;
            border-radius: 14px;
            padding: 0.2rem;
        }
        div[data-testid="stPlotlyChart"] .main-svg,
        div[data-testid="stPlotlyChart"] .svg-container {
            background: #ffffff !important;
        }
        div[data-testid="stAppViewContainer"],
        div[data-testid="stAppViewContainer"] p,
        div[data-testid="stAppViewContainer"] li,
        div[data-testid="stAppViewContainer"] span,
        div[data-testid="stAppViewContainer"] label,
        div[data-testid="stAppViewContainer"] small,
        div[data-testid="stAppViewContainer"] .stMarkdown,
        div[data-testid="stAppViewContainer"] .stCaption,
        div[data-testid="stAppViewContainer"] .stAlert,
        div[data-testid="stAppViewContainer"] .stInfo {
            color: var(--text-main) !important;
        }
        div[data-testid="stAppViewContainer"] a {
            color: #1b5fb8 !important;
        }
        .backtest-panel {
            border: 2px solid #d2ddf2;
            border-radius: 16px;
            background: var(--surface);
            padding: 0.9rem 1rem 0.4rem 1rem;
            box-shadow: 0 8px 24px rgba(20, 42, 90, 0.08);
            margin-top: 0.8rem;
            margin-bottom: 1rem;
        }
        .backtest-panel-title {
            font-size: 1.05rem;
            font-weight: 700;
            color: #102a5c;
            margin: 0.2rem 0 0.6rem 0;
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
            border: 2px solid #d4e0f5;
            border-radius: var(--card-radius);
            padding: 1rem 1.1rem;
            background: var(--surface);
            box-shadow: 0 8px 24px rgba(20, 42, 90, 0.08);
            min-height: 170px;
        }
        .metric-card.primary {
            background: linear-gradient(160deg, var(--navy-850) 0%, var(--navy-800) 100%);
            color: white;
            border-color: var(--navy-850);
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
        }
        .event-pill.gain {
            background: #dcfce7;
            color: #166534 !important;
        }
        .event-pill.loss {
            background: #fee2e2;
            color: #991b1b !important;
        }
        .event-pill.neutral {
            background: #e3ebfa;
            color: #1e3a8a !important;
        }
        .lc-refresh-badge {
            width: 78px;
            height: 78px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.97);
            border: 2px solid #ceddf6;
            box-shadow: 0 12px 30px rgba(7, 23, 51, .2);
            display: flex;
            align-items: center;
            justify-content: center;
            animation: lcPulse 0.9s ease-in-out infinite;
        }
        .lc-refresh-badge img {
            width: 54px;
            height: 54px;
            object-fit: contain;
            animation: lcFloat .9s ease-in-out infinite;
        }
        @keyframes lcPulse {
            0%,100% { transform: scale(1.0); box-shadow: 0 14px 34px rgba(7,23,51,.20); }
            50% { transform: scale(1.05); box-shadow: 0 22px 44px rgba(7,23,51,.27); }
        }
        @keyframes lcFloat {
            0%,100% { transform: translateY(0px); }
            50% { transform: translateY(-4px); }
        }
        @keyframes lcBadgeShow {
            0% { opacity: 0; transform: scale(.86); }
            20% { opacity: 1; transform: scale(1); }
            78% { opacity: 1; transform: scale(1); }
            100% { opacity: 0; transform: scale(.92); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fallback_logo_svg() -> str:
    return """
    <svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" viewBox="0 0 220 220">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#2f7fd0"/>
          <stop offset="100%" stop-color="#123f8f"/>
        </linearGradient>
      </defs>
      <circle cx="110" cy="110" r="104" fill="url(#g)"/>
      <path d="M160 84c-6-11-16-18-30-20-13-2-24 1-35 9-9 7-15 16-19 27-4 11-3 22 2 33 5 10 13 17 24 21 12 4 23 3 34-2 8-4 15-10 20-18l17 10-10-25 26-1-29-14z" fill="#fff"/>
      <text x="110" y="196" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" font-weight="700" fill="#ffffff">LC</text>
    </svg>
    """.strip()


@st.cache_data(show_spinner=False)
def get_refresh_logo_data_uri() -> str:
    candidates = [
        Path("assets/liberty_capital_logo.png"),
        Path("assets/liberty-capital-logo.png"),
        Path("screenshots/liberty_capital_logo.png"),
        Path("screenshots/liberty-capital-logo.png"),
        Path("output/liberty_capital_logo.png"),
        Path("output/liberty-capital-logo.png"),
    ]
    mime_by_ext = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}
    for logo_path in candidates:
        if not logo_path.exists():
            continue
        ext = logo_path.suffix.lower()
        mime = mime_by_ext.get(ext, "image/png")
        payload = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"
    fallback_svg = _fallback_logo_svg().encode("utf-8")
    payload = base64.b64encode(fallback_svg).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"


def render_refresh_logo_animation(visible: bool) -> None:
    if not visible:
        return
    logo = get_refresh_logo_data_uri()
    badge_id = f"lc-refresh-inline-{int(time.time() * 1000)}"
    st.markdown(
        f"""
        <div id="{badge_id}" style="position:fixed;top:86px;right:24px;width:92px;height:92px;z-index:999998;pointer-events:none;animation:lcBadgeShow 1.12s ease forwards;">
            <div class="lc-refresh-badge">
                <img src="{logo}" alt="Liberty Capital"/>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    logo_json = json.dumps(logo)
    components.html(
        f"""
        <script>
        (() => {{
          const doc = window.parent.document;
          const id = "lc-refresh-badge-active";
          const prev = doc.getElementById(id);
          if (prev) prev.remove();
          const host = doc.createElement("div");
          host.id = id;
          host.style.position = "fixed";
          host.style.top = "86px";
          host.style.right = "24px";
          host.style.width = "92px";
          host.style.height = "92px";
          host.style.zIndex = "999999";
          host.style.pointerEvents = "none";
          host.style.opacity = "0";
          host.style.transition = "opacity .18s ease";
          host.innerHTML = `
            <div class="lc-refresh-badge">
              <img src=${logo_json} alt="Liberty Capital"/>
            </div>
          `;
          doc.body.appendChild(host);
          requestAnimationFrame(() => {{ host.style.opacity = "1"; }});
          setTimeout(() => {{ host.style.opacity = "0"; }}, 760);
          setTimeout(() => {{ if (host.parentNode) host.parentNode.removeChild(host); }}, 1300);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def get_query_param_scalar(key: str) -> str:
    raw = st.query_params.get(key)
    if isinstance(raw, list):
        return str(raw[0]) if raw else ""
    return str(raw) if raw is not None else ""


def force_active_tab(tab_label: str | None) -> None:
    if not tab_label:
        return
    label_json = json.dumps(str(tab_label))
    components.html(
        f"""
        <script>
        (() => {{
          const target = {label_json};
          const clickTab = () => {{
            const doc = window.parent.document;
            const buttons = doc.querySelectorAll('button[data-baseweb="tab"]');
            for (const btn of buttons) {{
              if ((btn.textContent || "").trim() === target) {{
                btn.click();
                return true;
              }}
            }}
            return false;
          }};
          clickTab();
          setTimeout(clickTab, 120);
          setTimeout(clickTab, 420);
          setTimeout(clickTab, 900);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def sync_tabs_state(default_tab: str) -> None:
    labels_json = json.dumps(MAIN_TAB_LABELS)
    default_json = json.dumps(default_tab if default_tab in MAIN_TAB_LABELS else MAIN_TAB_LABELS[0])
    components.html(
        f"""
        <script>
        (() => {{
          const labels = {labels_json};
          const fallback = {default_json};
          const key = "lc_active_main_tab";
          const rootWin = window.parent;
          const doc = rootWin.document;

          const saveTab = (label) => {{
            if (!labels.includes(label)) return;
            try {{
              rootWin.localStorage.setItem(key, label);
            }} catch (e) {{}}
            try {{
              const url = new URL(rootWin.location.href);
              url.searchParams.set("onglet", label);
              rootWin.history.replaceState({{}}, "", url.toString());
            }} catch (e) {{}}
          }};

          const resolveWanted = () => {{
            let wanted = fallback;
            try {{
              const url = new URL(rootWin.location.href);
              const fromUrl = (url.searchParams.get("onglet") || "").trim();
              if (labels.includes(fromUrl)) {{
                wanted = fromUrl;
              }} else {{
                const saved = (rootWin.localStorage.getItem(key) || "").trim();
                if (labels.includes(saved)) wanted = saved;
              }}
            }} catch (e) {{}}
            return wanted;
          }};

          const clickWanted = () => {{
            const wanted = resolveWanted();
            const buttons = doc.querySelectorAll('button[data-baseweb="tab"]');
            for (const btn of buttons) {{
              const label = (btn.textContent || "").trim();
              if (label === wanted) {{
                if (btn.getAttribute("aria-selected") !== "true") {{
                  btn.click();
                }}
                saveTab(label);
                return true;
              }}
            }}
            return false;
          }};

          const bindClicks = () => {{
            const buttons = doc.querySelectorAll('button[data-baseweb="tab"]');
            for (const btn of buttons) {{
              if (btn.dataset.lcTabBound === "1") continue;
              btn.dataset.lcTabBound = "1";
              btn.addEventListener("click", () => {{
                const label = (btn.textContent || "").trim();
                saveTab(label);
              }});
            }}
          }};

          bindClicks();
          clickWanted();
          setTimeout(() => {{ bindClicks(); clickWanted(); }}, 120);
          setTimeout(() => {{ bindClicks(); clickWanted(); }}, 420);
          setTimeout(() => {{ bindClicks(); clickWanted(); }}, 900);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def apply_plot_theme(
    fig: go.Figure,
    *,
    title: str,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    margin_top: int = 48,
) -> go.Figure:
    fig.update_layout(
        title=title,
        template=None,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={"color": "#102a5c"},
        colorway=["#103b88", "#1f5cb5", "#0f9d58", "#d93025", "#6d83aa"],
        legend={"bgcolor": "rgba(255,255,255,0.85)", "bordercolor": "#d7e3f8", "borderwidth": 1},
        margin={"l": 106, "r": 30, "t": margin_top, "b": 66},
        uniformtext={"minsize": 10, "mode": "hide"},
    )
    fig.update_xaxes(
        title=xaxis_title,
        title_standoff=12,
        automargin=True,
        showgrid=True,
        gridcolor="#dbe5f5",
        linecolor="#bfd0ea",
        zeroline=False,
        ticks="outside",
        tickfont={"size": 12},
        tickangle=-20,
        nticks=6,
        ticklabeloverflow="allow",
    )
    fig.update_yaxes(
        title=yaxis_title,
        title_standoff=16,
        automargin=True,
        showgrid=True,
        gridcolor="#dbe5f5",
        linecolor="#bfd0ea",
        zeroline=False,
        ticks="outside",
        tickfont={"size": 11},
        nticks=8,
        ticklabeloverflow="allow",
    )
    return fig


def render_metric_card(
    title: str,
    value: str,
    subtitle: str,
    primary: bool = False,
    badge: str | None = None,
) -> None:
    card_class = "metric-card primary" if primary else "metric-card"
    badge_class = "neutral"
    if badge:
        raw = str(badge).strip()
        if raw.startswith("-"):
            badge_class = "loss"
        elif raw.startswith("+"):
            badge_class = "gain"
    badge_html = f"<div class='event-pill {badge_class}'>{badge}</div>" if badge else ""
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
        apply_plot_theme(fig, title="Aucun instantané disponible")
        return fig
    points = snapshots.copy()
    points["captured_local"] = pd.to_datetime(points["captured_at_utc"], utc=True).dt.tz_convert(DISPLAY_TZ)
    fig.add_trace(
        go.Scatter(
            x=points["captured_local"],
            y=points["portfolio_value"],
            mode="lines",
            line={"color": "#103b88", "width": 3},
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
                    "line": {"color": "#ffffff", "width": 1},
                },
                text=marks["event_label"].fillna(marks["event_type"]),
                hovertemplate=f"%{{x|%d/%m/%Y %H:%M}}<br>%{{y:.2f}} {currency}<br>%{{text}}<extra></extra>",
                name="Événements",
            )
        )
    apply_plot_theme(
        fig,
        title="Évolution du portefeuille (instantanés achats/ventes/hausses/baisses)",
        xaxis_title="Date",
        yaxis_title=f"Valeur ({currency})",
        margin_top=50,
    )
    fig.update_xaxes(nticks=5, tickformat="%H:%M\n%d/%m")
    return fig


def create_allocation_chart(data: pd.DataFrame, label_col: str, value_col: str, title: str) -> go.Figure:
    if data.empty or float(data[value_col].sum()) <= 0:
        fig = go.Figure()
        apply_plot_theme(fig, title=f"{title} (vide)")
        return fig
    fig = px.pie(
        data,
        names=label_col,
        values=value_col,
        hole=0.55,
        title=title,
        color_discrete_sequence=[
            "#0f9d58",
            "#d93025",
            "#1a4aa5",
            "#16a34a",
            "#ef4444",
            "#2f6ccc",
            "#22c55e",
            "#b91c1c",
            "#6c8ec4",
        ],
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent+label",
        marker={"line": {"color": "#ffffff", "width": 1}},
    )
    apply_plot_theme(fig, title=title, margin_top=55)
    return fig


def create_drawdown_chart(snapshots: pd.DataFrame, currency: str = "EUR") -> go.Figure:
    fig = go.Figure()
    if snapshots.empty:
        apply_plot_theme(fig, title="Repli maximal indisponible")
        return fig
    pts = snapshots.copy()
    pts["captured_local"] = pd.to_datetime(pts["captured_at_utc"], utc=True).dt.tz_convert(DISPLAY_TZ)
    values = pd.to_numeric(pts["portfolio_value"], errors="coerce")
    peak = values.cummax()
    drawdown = (values / peak - 1.0) * 100
    fig.add_trace(
        go.Scatter(
            x=pts["captured_local"],
            y=drawdown,
            mode="lines",
            line={"color": "#dc2626", "width": 2.4},
            fill="tozeroy",
            fillcolor="rgba(217,48,37,0.20)",
            name="Repli maximal",
        )
    )
    recovery = drawdown.where(drawdown > -1.0, np.nan)
    if not recovery.dropna().empty:
        fig.add_trace(
            go.Scatter(
                x=pts["captured_local"],
                y=recovery,
                mode="lines",
                line={"color": "#0f9d58", "width": 2.0},
                name="Zone récupération",
            )
        )
    apply_plot_theme(
        fig,
        title="Repli maximal du portefeuille",
        xaxis_title="Date",
        yaxis_title="Repli maximal %",
        margin_top=45,
    )
    fig.add_hline(y=0, line={"color": "#6d83aa", "dash": "dot"})
    return fig


def create_pnl_contribution_chart(holdings: pd.DataFrame, transactions: pd.DataFrame, base_currency: str) -> go.Figure:
    if holdings is None or holdings.empty:
        fig = go.Figure()
        fig.update_layout(title="Contribution gain/perte indisponible")
        return fig
    h = holdings.copy()
    h["latent"] = pd.to_numeric(h["pnl_latent"], errors="coerce").fillna(0.0)
    h["realized_live"] = pd.to_numeric(h["pnl_realise"], errors="coerce").fillna(0.0)
    h["realized_hist"] = pd.to_numeric(h["pnl_realise_historique"], errors="coerce").fillna(0.0)
    h["fx_effect"] = h["realized_live"] - h["realized_hist"]
    fees_map: dict[str, float] = {}
    if transactions is not None and not transactions.empty:
        tx = transactions.copy()
        for row in tx.itertuples(index=False):
            status = str(getattr(row, "execution_status", "FILLED")).upper()
            if status not in {"FILLED", "PARTIAL"}:
                continue
            sym = str(row.symbol).upper()
            fees_base = safe_float(getattr(row, "fees", 0.0), 0.0) * safe_float(getattr(row, "fx_to_base", 1.0), 1.0)
            fees_map[sym] = fees_map.get(sym, 0.0) + fees_base
    h["fees"] = h["symbol"].map(lambda s: -safe_float(fees_map.get(str(s).upper(), 0.0), 0.0))
    h["net_total"] = h["latent"] + h["realized_live"] + h["fees"]

    fig = go.Figure()
    latent_color = h["latent"].map(lambda v: "#16a34a" if safe_float(v, 0.0) >= 0 else "#dc2626").tolist()
    realized_color = h["realized_live"].map(lambda v: "#0f9d58" if safe_float(v, 0.0) >= 0 else "#ef4444").tolist()
    fx_color = h["fx_effect"].map(lambda v: "#22c55e" if safe_float(v, 0.0) >= 0 else "#f87171").tolist()
    fee_color = ["#b91c1c"] * len(h)
    fig.add_trace(go.Bar(x=h["symbol"], y=h["latent"], name="Gain/Perte latent", marker={"color": latent_color}))
    fig.add_trace(go.Bar(x=h["symbol"], y=h["realized_live"], name="Gain/Perte réalisé (base temps réel)", marker={"color": realized_color}))
    fig.add_trace(go.Bar(x=h["symbol"], y=h["fx_effect"], name="Effet FX", marker={"color": fx_color}))
    fig.add_trace(go.Bar(x=h["symbol"], y=h["fees"], name="Frais", marker={"color": fee_color}))
    apply_plot_theme(
        fig,
        title=f"Contribution gain/perte par actif ({base_currency})",
        xaxis_title="Actif",
        yaxis_title=f"Contrib. ({base_currency})",
        margin_top=45,
    )
    fig.update_layout(barmode="relative")
    fig.update_xaxes(tickangle=-32, nticks=10)
    return fig


def create_benchmark_relative_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df is None or df.empty or "date" not in df.columns:
        apply_plot_theme(fig, title="Indice de référence vs portefeuille indisponible")
        return fig
    x = pd.to_datetime(df["date"], errors="coerce")
    if "equity" in df.columns:
        s = pd.to_numeric(df["equity"], errors="coerce")
        if not s.dropna().empty:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=s / s.dropna().iloc[0] * 100,
                    mode="lines",
                    name="Portefeuille (base 100)",
                    line={"color": "#103b88", "width": 2.6},
                )
            )
    if "benchmark_equity" in df.columns:
        b = pd.to_numeric(df["benchmark_equity"], errors="coerce")
        if not b.dropna().empty:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=b / b.dropna().iloc[0] * 100,
                    mode="lines",
                    name="Indice de référence (base 100)",
                    line={"color": "#d93025", "width": 2.1, "dash": "dot"},
                )
            )
    apply_plot_theme(
        fig,
        title="Performance relative vs indice de référence",
        xaxis_title="Date",
        yaxis_title="Base 100",
        margin_top=45,
    )
    fig.update_xaxes(tickformat="%b %Y", tickangle=0, nticks=8)
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


def render_positions_portefeuille(holdings: pd.DataFrame, base_currency: str, split_factors: dict[str, float]) -> None:
    st.markdown("#### Positions en portefeuille")
    split_adjusted = {k: v for k, v in split_factors.items() if abs(safe_float(v, 1.0) - 1.0) > 1e-9}
    if split_adjusted:
        st.caption(
            "Ajustements split appliqués automatiquement: "
            + ", ".join([f"{sym} x{safe_float(fac, 1.0):.4f}" for sym, fac in split_adjusted.items()])
        )
    if holdings.empty:
        st.info("Aucune position ouverte.")
        return

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
            "pnl_total_live",
            "pnl_realise_historique",
        ]
    ].copy()
    render_dataframe_fr(
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
                "fx_to_base": f"FX vers {base_currency}",
                "valeur_marche_devise": "Valeur devise",
                "valeur_marche": "Valeur marché",
                "pnl_latent": "Gain/Perte latent (temps réel)",
                "pnl_realise": "Gain/Perte réalisé (base temps réel)",
                "pnl_total_live": "Gain/Perte total (temps réel)",
                "pnl_realise_historique": "Gain/Perte réalisé (historique)",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )


def resolve_quick_sell_quantity(
    mode: str,
    held_qty: float,
    held_value_base: float,
    unit_price_base: float,
    qty_input: float,
    amount_input: float,
    pct_input: float,
) -> tuple[float, str]:
    qty_held = max(safe_float(held_qty, 0.0), 0.0)
    if qty_held <= 0:
        return 0.0, "Position vide."

    mode_norm = str(mode).strip().lower()
    target_qty = 0.0
    if mode_norm == "position_totale":
        target_qty = qty_held
    elif mode_norm == "quantite":
        target_qty = max(safe_float(qty_input, 0.0), 0.0)
    elif mode_norm == "montant_base":
        px_base = max(safe_float(unit_price_base, 0.0), 0.0)
        amount = max(safe_float(amount_input, 0.0), 0.0)
        if px_base <= 0:
            return 0.0, "Prix en devise portefeuille indisponible."
        target_qty = amount / px_base
    elif mode_norm == "pourcentage":
        pct_value = max(min(safe_float(pct_input, 0.0), 100.0), 0.0)
        target_qty = qty_held * pct_value / 100.0
    else:
        return 0.0, "Mode de vente inconnu."

    target_qty = max(target_qty, 0.0)
    if target_qty > qty_held:
        capped = qty_held
        if mode_norm == "montant_base" and held_value_base > 0:
            return capped, "Montant supérieur à la position: quantité ramenée à une vente totale."
        return capped, "Quantité supérieure à la position: quantité ramenée au maximum disponible."
    return target_qty, ""


def render_quick_sell_panel(
    *,
    conn: sqlite3.Connection,
    holdings: pd.DataFrame,
    quotes: pd.DataFrame,
    fx_rates: dict[str, float],
    state: dict[str, float],
    trade_slippage_bps: float,
    trade_spread_bps: float,
    exchange_default: str,
    key_prefix: str,
) -> None:
    st.markdown("#### Vente rapide")
    st.caption("Exécute une vente depuis une position existante, sans ressaisir tout le ticket d'ordre.")
    if holdings is None or holdings.empty:
        st.info("Aucune position disponible pour la vente rapide.")
        return

    qmap = quotes.set_index("symbol").to_dict(orient="index") if quotes is not None and not quotes.empty else {}
    rows = holdings.copy()
    rows["label"] = rows.apply(
        lambda r: f"{r['symbol']} · {r['nom']} · qty {safe_float(r['quantite'], 0.0):.4f}", axis=1
    )
    symbols = rows["symbol"].astype(str).tolist()
    labels_by_symbol = dict(zip(rows["symbol"].astype(str), rows["label"].astype(str)))

    with st.form(f"{key_prefix}_form_quick_sell"):
        symbol = st.selectbox(
            "Position à vendre",
            symbols,
            format_func=lambda s: labels_by_symbol.get(str(s), str(s)),
            key=f"{key_prefix}_symbol",
        )
        selected = rows[rows["symbol"].astype(str) == str(symbol)]
        pos = selected.iloc[0] if not selected.empty else rows.iloc[0]
        held_qty = max(safe_float(pos.get("quantite", 0.0), 0.0), 0.0)
        quote = qmap.get(str(symbol), {})
        unit_quote = safe_float(quote.get("last", pos.get("cours", np.nan)), np.nan)
        quote_currency = infer_currency(str(symbol), str(quote.get("currency", pos.get("devise", ""))), state["base_currency"])
        fx_to_base = safe_float(fx_rates.get(quote_currency, np.nan), np.nan)
        if np.isnan(fx_to_base) or fx_to_base <= 0:
            fx_to_base = safe_float(pos.get("fx_to_base", 1.0), 1.0)
        unit_base = safe_float(unit_quote, 0.0) * safe_float(fx_to_base, 1.0)
        held_value_base = safe_float(pos.get("valeur_marche", held_qty * unit_base), held_qty * unit_base)

        st.caption(
            f"Position: {held_qty:.4f} parts | Dernier cours: {safe_float(unit_quote, 0.0):.4f} {quote_currency} "
            f"| Valeur: {money(held_value_base, state['base_currency'])}"
        )

        mode = st.selectbox(
            "Mode de vente",
            ["position_totale", "quantite", "montant_base", "pourcentage"],
            format_func=lambda m: {
                "position_totale": "Vente totale de la position",
                "quantite": "Vente par quantité (parts)",
                "montant_base": f"Vente par montant ({state['base_currency']})",
                "pourcentage": "Vente par pourcentage de la position",
            }.get(str(m), str(m)),
            key=f"{key_prefix}_mode",
        )

        m1, m2 = st.columns(2)
        with m1:
            qty_input = st.number_input(
                "Quantité à vendre",
                min_value=0.0,
                value=min(held_qty, 1.0) if held_qty > 0 else 0.0,
                step=0.1,
                disabled=(mode != "quantite"),
                key=f"{key_prefix}_qty",
            )
        with m2:
            amount_input = st.number_input(
                f"Montant à vendre ({state['base_currency']})",
                min_value=0.0,
                value=max(held_value_base * 0.25, 0.0),
                step=10.0,
                disabled=(mode != "montant_base"),
                key=f"{key_prefix}_amount",
            )
        pct_input = st.slider(
            "Pourcentage de la position à vendre (%)",
            min_value=1,
            max_value=100,
            value=50,
            disabled=(mode != "pourcentage"),
            key=f"{key_prefix}_pct",
        )

        o1, o2 = st.columns(2)
        with o1:
            order_type = st.selectbox(
                "Type d'ordre",
                ["MARKET", "LIMIT", "STOP"],
                format_func=lambda x: localize_text_fr(x),
                key=f"{key_prefix}_order_type",
            )
        with o2:
            trigger_price = st.number_input(
                "Prix déclenchement",
                min_value=0.0,
                value=float(safe_float(unit_quote, 0.0)) if order_type != "MARKET" else 0.0,
                step=0.01,
                disabled=(order_type == "MARKET"),
                key=f"{key_prefix}_trigger",
            )

        x1, x2, x3 = st.columns(3)
        with x1:
            fees = st.number_input("Frais", min_value=0.0, value=0.0, step=0.01, key=f"{key_prefix}_fees")
        with x2:
            slippage_bps = st.number_input(
                "Glissement (bps)",
                min_value=0.0,
                value=float(trade_slippage_bps),
                step=0.5,
                key=f"{key_prefix}_slippage",
            )
        with x3:
            spread_bps = st.number_input(
                "Écart achat/vente (bps)",
                min_value=0.0,
                value=float(trade_spread_bps),
                step=0.5,
                key=f"{key_prefix}_spread",
            )

        trade_exchange = st.selectbox(
            "Marché",
            ["XNYS", "XPAR", "XHKG", "XTKS"],
            index=["XNYS", "XPAR", "XHKG", "XTKS"].index(exchange_default) if exchange_default in {"XNYS", "XPAR", "XHKG", "XTKS"} else 0,
            key=f"{key_prefix}_exchange",
        )
        note = st.text_input("Note (optionnelle)", key=f"{key_prefix}_note")
        submitted = st.form_submit_button("Vendre la position", use_container_width=True)

        if submitted:
            qty_target, qty_msg = resolve_quick_sell_quantity(
                mode=mode,
                held_qty=held_qty,
                held_value_base=held_value_base,
                unit_price_base=unit_base,
                qty_input=float(qty_input),
                amount_input=float(amount_input),
                pct_input=float(pct_input),
            )
            if qty_msg:
                st.warning(qty_msg)
            if qty_target <= 0:
                st.error("Quantité de vente calculée invalide.")
                return
            if np.isnan(unit_quote) or safe_float(unit_quote, 0.0) <= 0:
                st.error("Cours de marché indisponible. Vente rapide impossible.")
                return

            execution = simulate_order_execution(
                side="SELL",
                order_type=order_type,
                market_price=float(unit_quote),
                quantity=float(qty_target),
                trigger_price=(float(trigger_price) if order_type != "MARKET" else None),
                slippage_bps=float(slippage_bps),
                spread_bps=float(spread_bps),
                symbol=str(symbol),
            )
            exec_status = str(execution.get("execution_status", "PENDING")).upper()
            exec_qty = float(execution.get("executed_quantity", 0.0))
            exec_price = float(execution.get("executed_price", unit_quote))
            fill_ratio = float(execution.get("fill_ratio", 0.0))
            fees_exec = float(fees) * fill_ratio

            risk_errors: list[str] = []
            if exec_status in {"FILLED", "PARTIAL"}:
                risk_errors = check_trade_risk(
                    side="SELL",
                    symbol=str(symbol),
                    quantity=float(exec_qty),
                    price=float(exec_price),
                    fees=float(fees_exec),
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
                log_event(
                    conn,
                    "WARNING",
                    "quick_sell_blocked_risk",
                    {"symbol": str(symbol), "errors": risk_errors, "mode": mode},
                )
                return

            insert_transaction(
                conn,
                symbol=str(symbol),
                side="SELL",
                quantity=float(qty_target),
                price=float(unit_quote),
                fees=float(fees_exec),
                currency=quote_currency,
                fx_to_base=float(fx_to_base),
                exchange=trade_exchange,
                strategy_tag="vente_rapide",
                note=note,
                order_type=order_type,
                trigger_price=float(trigger_price) if order_type != "MARKET" else None,
                execution_status=exec_status,
                fill_ratio=fill_ratio,
                executed_quantity=exec_qty,
                executed_price=exec_price,
                slippage_bps=float(slippage_bps),
                spread_bps=float(spread_bps),
            )
            if exec_status in {"FILLED", "PARTIAL"}:
                st.session_state["pending_snapshot_event"] = {
                    "type": "SELL",
                    "label": f"Vente rapide {exec_qty:g}/{qty_target:g} {symbol} @ {exec_price:.2f} {quote_currency}",
                }
            else:
                st.session_state["pending_snapshot_event"] = None
            log_event(
                conn,
                "INFO",
                "quick_sell_inserted",
                {
                    "symbol": str(symbol),
                    "mode": mode,
                    "qty_target": float(qty_target),
                    "execution_status": exec_status,
                    "executed_qty": exec_qty,
                    "executed_price": exec_price,
                },
            )
            fetch_quotes.clear()
            fetch_realtime_quotes.clear()
            fetch_polygon_snapshot_quotes.clear()
            if exec_status == "PENDING":
                st.warning("Ordre de vente rapide enregistré en statut En attente.")
            else:
                st.success(f"Vente rapide enregistrée ({localize_text_fr(exec_status)}).")
            st.rerun()


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
    # Normalise un portefeuille vide/partiel pour éviter les KeyError
    # (cas fréquent avant la première transaction).
    if isinstance(holdings, pd.DataFrame):
        holdings_norm = holdings.copy()
    else:
        holdings_norm = pd.DataFrame()
    for col, default in (("symbol", ""), ("zone", "USA"), ("secteur", "Non classé"), ("valeur_marche", 0.0)):
        if col not in holdings_norm.columns:
            holdings_norm[col] = default
    holdings_norm["symbol"] = holdings_norm["symbol"].astype(str)
    holdings_norm["valeur_marche"] = pd.to_numeric(holdings_norm["valeur_marche"], errors="coerce").fillna(0.0)

    buy_cost_base = (quantity * price + fees) * fx_to_base
    sell_proceeds_base = max(quantity * price - fees, 0.0) * fx_to_base
    current_value = float(holdings_norm["valeur_marche"].sum()) if not holdings_norm.empty else 0.0
    total_after = cash + current_value
    if side.upper() == "BUY":
        if cash < buy_cost_base:
            errors.append(f"Cash insuffisant: requis {money(buy_cost_base, base_currency)}, disponible {money(cash, base_currency)}.")
        projected_total = total_after
        symbol_value = float(holdings_norm.loc[holdings_norm["symbol"] == symbol, "valeur_marche"].sum()) + buy_cost_base
    else:
        projected_total = total_after
        symbol_value = max(
            0.0,
            float(holdings_norm.loc[holdings_norm["symbol"] == symbol, "valeur_marche"].sum()) - sell_proceeds_base,
        )

    if projected_total <= 0:
        return errors

    line_pct = (symbol_value / projected_total) * 100
    if line_pct > max_line_pct:
        errors.append(f"Limite ligne dépassée ({line_pct:.1f}% > {max_line_pct:.1f}%).")

    if not holdings_norm.empty:
        tmp = holdings_norm.copy()
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
        if insert_alert(conn, "drawdown", "HIGH", "Repli maximal critique", f"Repli maximal courant: {drawdown_pct:.2f}%"):
            fired.append("Repli maximal critique")
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


def _deterministic_backtest_fill_ratio(symbol: str, date_key: str, strategy: str) -> float:
    low = 0.72 if strategy == "sma50" else 0.86
    key = f"{symbol}|{date_key}|{strategy}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    rnd = int.from_bytes(digest[:8], "big") / float(2**64)
    return float(low + (1.0 - low) * rnd)


def apply_partial_execution_to_weights(target_weights: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if target_weights is None or target_weights.empty:
        return pd.DataFrame()
    executed = pd.DataFrame(index=target_weights.index, columns=target_weights.columns, dtype=float)
    previous = pd.Series(0.0, index=target_weights.columns, dtype=float)
    for dt, row in target_weights.iterrows():
        date_key = pd.Timestamp(dt).date().isoformat()
        target = pd.to_numeric(row, errors="coerce").fillna(0.0).clip(lower=0.0)
        next_weights = target.copy()
        for symbol in target.index:
            ratio = _deterministic_backtest_fill_ratio(str(symbol), date_key, strategy=strategy)
            next_weights[symbol] = previous.get(symbol, 0.0) + (target[symbol] - previous.get(symbol, 0.0)) * ratio
        total = float(next_weights.sum())
        if total > 1.0:
            next_weights = next_weights / total
        executed.loc[dt] = next_weights
        previous = next_weights
    return executed.fillna(0.0)


def load_backtest_runs(conn: sqlite3.Connection, limit: int = 30) -> pd.DataFrame:
    raw = pd.read_sql_query(
        """
        SELECT id, created_at_utc, strategy, symbols_csv, start_date, end_date, initial_capital, metrics_json, curve_json, benchmark
        FROM backtest_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(int(limit),),
        parse_dates=["created_at_utc"],
    )
    if raw.empty:
        return raw
    metric_rows: list[dict] = []
    for row in raw.itertuples(index=False):
        payload = {}
        try:
            payload = json.loads(str(row.metrics_json or "{}"))
        except Exception:
            payload = {}
        payload["id"] = int(row.id)
        metric_rows.append(payload)
    metrics_df = pd.DataFrame(metric_rows) if metric_rows else pd.DataFrame(columns=["id"])
    merged = raw.merge(metrics_df, on="id", how="left")
    return merged


def parse_curve_json(curve_json: str | None) -> pd.DataFrame:
    if not curve_json:
        return pd.DataFrame()
    try:
        rows = json.loads(curve_json)
    except Exception:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    return df


def dataframe_records_json_safe(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            ts = pd.to_datetime(out[col], errors="coerce", utc=True)
            out[col] = ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.where(pd.notna(out), None)
    return out.to_dict(orient="records")


def build_rebalance_plan(
    holdings: pd.DataFrame,
    state: dict[str, float],
    max_line_pct: float,
    max_sector_pct: float,
    max_zone_pct: float,
) -> pd.DataFrame:
    columns = ["action", "symbol", "notional_base", "reason", "priority"]
    if holdings is None or holdings.empty:
        return pd.DataFrame(columns=columns)
    total = safe_float(state.get("portfolio_value", 0.0), 0.0)
    if total <= 0:
        total = safe_float(pd.to_numeric(holdings["valeur_marche"], errors="coerce").sum(), 0.0)
    if total <= 0:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    h = holdings.copy()
    h["valeur_marche"] = pd.to_numeric(h["valeur_marche"], errors="coerce").fillna(0.0)

    line_cap = total * (max_line_pct / 100.0)
    for row in h.itertuples(index=False):
        excess = float(row.valeur_marche) - line_cap
        if excess > 1.0:
            rows.append(
                {
                    "action": "SELL",
                    "symbol": str(row.symbol),
                    "notional_base": float(excess),
                    "reason": f"Dépassement limite ligne ({max_line_pct:.1f}%)",
                    "priority": 1,
                }
            )

    sector_sum = h.groupby("secteur", as_index=False)["valeur_marche"].sum()
    for sec_row in sector_sum.itertuples(index=False):
        cap = total * (max_sector_pct / 100.0)
        excess = float(sec_row.valeur_marche) - cap
        if excess <= 1.0:
            continue
        sector_holdings = h[h["secteur"] == sec_row.secteur].sort_values("valeur_marche", ascending=False)
        if sector_holdings.empty:
            continue
        largest = sector_holdings.iloc[0]
        rows.append(
            {
                "action": "SELL",
                "symbol": str(largest["symbol"]),
                "notional_base": float(excess),
                "reason": f"Dépassement limite secteur ({max_sector_pct:.1f}%)",
                "priority": 1,
            }
        )

    zone_sum = h.groupby("zone", as_index=False)["valeur_marche"].sum()
    for zone_row in zone_sum.itertuples(index=False):
        cap = total * (max_zone_pct / 100.0)
        excess = float(zone_row.valeur_marche) - cap
        if excess <= 1.0:
            continue
        zone_holdings = h[h["zone"] == zone_row.zone].sort_values("valeur_marche", ascending=False)
        if zone_holdings.empty:
            continue
        largest = zone_holdings.iloc[0]
        rows.append(
            {
                "action": "SELL",
                "symbol": str(largest["symbol"]),
                "notional_base": float(excess),
                "reason": f"Dépassement limite zone ({max_zone_pct:.1f}%)",
                "priority": 2,
            }
        )

    cash = safe_float(state.get("cash", 0.0), 0.0)
    if cash > total * 0.10:
        underweighted = h.sort_values("valeur_marche", ascending=True).head(3)
        if not underweighted.empty:
            per_line = float(min(cash * 0.50, total * 0.15) / len(underweighted))
            for row in underweighted.itertuples(index=False):
                rows.append(
                    {
                        "action": "BUY",
                        "symbol": str(row.symbol),
                        "notional_base": per_line,
                        "reason": "Réduire la concentration et déployer le cash",
                        "priority": 3,
                    }
                )
    if not rows:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(rows).sort_values(["priority", "notional_base"], ascending=[True, False]).reset_index(drop=True)
    return out[columns]


def run_backtest(
    symbols: list[str],
    start: str,
    end: str,
    initial_capital: float,
    strategy: str = "buy_hold",
    exchange: str = DEFAULT_EXCHANGE,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    fees_bps: float = 8.0,
    slippage_bps: float = 5.0,
) -> tuple[pd.DataFrame, dict[str, float]]:
    clean_symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not clean_symbols:
        return pd.DataFrame(), {}
    benchmark = str(benchmark_symbol or DEFAULT_BENCHMARK_SYMBOL).strip().upper()
    all_symbols = list(dict.fromkeys(clean_symbols + ([benchmark] if benchmark else [])))
    prices_raw = fetch_history_for_backtest(tuple(all_symbols), start, end)
    if prices_raw.empty:
        return pd.DataFrame(), {}

    available_assets = [s for s in clean_symbols if s in prices_raw.columns]
    if not available_assets:
        return pd.DataFrame(), {}

    prices = prices_raw[available_assets].dropna(how="all").ffill().dropna()
    if prices.empty:
        return pd.DataFrame(), {}

    try:
        filtered = filter_prices_to_market_sessions(prices, exchange=exchange)
        if filtered is not None and not filtered.empty:
            prices = filtered
    except Exception:
        pass
    if prices.empty:
        return pd.DataFrame(), {}

    returns = prices.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if strategy == "sma50":
        signals = (prices > prices.rolling(50).mean()).astype(float).fillna(0.0)
        active = signals.sum(axis=1).replace(0, np.nan)
        target_weights = signals.div(active, axis=0).fillna(0.0)
    else:
        n = prices.shape[1]
        target_weights = pd.DataFrame(1.0 / n, index=prices.index, columns=prices.columns)

    executed_weights = apply_partial_execution_to_weights(target_weights, strategy=strategy)
    prev_weights = executed_weights.shift(1).fillna(0.0)
    gross_returns = (prev_weights * returns).sum(axis=1)
    turnover = (executed_weights - prev_weights).abs().sum(axis=1)
    fee_rate = max(0.0, safe_float(fees_bps, 0.0) + safe_float(slippage_bps, 0.0)) / 10_000.0
    costs_ret = turnover * fee_rate
    net_returns = gross_returns - costs_ret

    equity = float(initial_capital) * (1 + net_returns).cumprod()
    max_equity = equity.cummax()
    drawdown = equity / max_equity - 1.0
    prev_equity = equity.shift(1).fillna(float(initial_capital))
    cost_value = prev_equity * costs_ret

    benchmark_equity = pd.Series(np.nan, index=equity.index)
    benchmark_col = benchmark if benchmark in prices_raw.columns else None
    if benchmark_col:
        bench_prices = pd.to_numeric(prices_raw[benchmark_col], errors="coerce").reindex(equity.index).ffill().dropna()
        if not bench_prices.empty:
            benchmark_ret = bench_prices.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
            benchmark_equity = float(initial_capital) * (1 + benchmark_ret).cumprod()
            benchmark_equity = benchmark_equity.reindex(equity.index).ffill()

    out = pd.DataFrame(
        {
            "date": equity.index,
            "equity": equity.values,
            "returns": net_returns.values,
            "drawdown": drawdown.values,
            "turnover": turnover.values,
            "cost_value": cost_value.values,
            "benchmark_equity": benchmark_equity.values,
        }
    )

    obs = max(len(out) - 1, 1)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (252.0 / obs) - 1.0 if len(out) > 1 and equity.iloc[0] > 0 else 0.0
    vol = float(net_returns.std(ddof=1) * np.sqrt(252)) if len(net_returns) > 1 else 0.0
    sharpe = float((net_returns.mean() / net_returns.std(ddof=1) * np.sqrt(252)) if net_returns.std(ddof=1) > 0 else 0.0)
    max_dd = float(drawdown.min() * 100) if not drawdown.empty else 0.0

    relative_perf = np.nan
    if benchmark_col and not benchmark_equity.dropna().empty and benchmark_equity.iloc[-1] > 0:
        relative_perf = float((equity.iloc[-1] / benchmark_equity.iloc[-1] - 1.0) * 100.0)
        out["relative_vs_benchmark_pct"] = (out["equity"] / out["benchmark_equity"] - 1.0) * 100.0

    metrics = {
        "annual_return_pct": float(cagr * 100.0),
        "volatility_pct": float(vol * 100.0),
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd,
        "final_value": float(equity.iloc[-1]),
        "cum_fees_slippage": float(cost_value.sum()),
        "avg_turnover_pct": float(turnover.mean() * 100.0),
        "benchmark": benchmark_col or "",
        "relative_vs_benchmark_pct": float(relative_perf) if not np.isnan(relative_perf) else np.nan,
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
    st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")
    render_css()
    auth_payload = get_base44_auth_payload()
    auth_email = str(auth_payload.get("email", "")).strip().lower() if auth_payload else ""
    db_path = DB_PATH
    if auth_email:
        db_path = user_db_path(auth_email)
        conn = get_connection_for_user(auth_email)
    else:
        conn = get_connection()
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
    if "auto_refresh_enabled" not in st.session_state:
        st.session_state["auto_refresh_enabled"] = get_setting(conn, "auto_refresh_enabled", "0") == "1"
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
    if "backtest_result" not in st.session_state:
        st.session_state["backtest_result"] = None
    if "benchmark_symbol" not in st.session_state:
        st.session_state["benchmark_symbol"] = get_setting(conn, "benchmark_symbol", DEFAULT_BENCHMARK_SYMBOL).upper()
    if "trade_slippage_bps" not in st.session_state:
        st.session_state["trade_slippage_bps"] = get_setting_float(conn, "trade_slippage_bps", DEFAULT_SIM_SLIPPAGE_BPS)
    if "trade_spread_bps" not in st.session_state:
        st.session_state["trade_spread_bps"] = get_setting_float(conn, "trade_spread_bps", DEFAULT_SIM_SPREAD_BPS)
    if "backtest_fees_bps" not in st.session_state:
        st.session_state["backtest_fees_bps"] = get_setting_float(conn, "backtest_fees_bps", 8.0)
    if "backtest_slippage_bps" not in st.session_state:
        st.session_state["backtest_slippage_bps"] = get_setting_float(conn, "backtest_slippage_bps", 5.0)
    if "quote_error_cache" not in st.session_state:
        st.session_state["quote_error_cache"] = {}
    if "last_autorefresh_count" not in st.session_state:
        st.session_state["last_autorefresh_count"] = -1
    if "pending_tab_focus" not in st.session_state:
        st.session_state["pending_tab_focus"] = ""

    st.markdown(f"<div class='main-title'>{APP_TITLE}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='subtitle'>{APP_SUBTITLE}</div>", unsafe_allow_html=True)
    if auth_email:
        st.caption(f"Session Base44: {auth_email}")
    elif get_auth_mode() == "required":
        st.caption("Session sécurisée activée.")

    with st.sidebar:
        st.subheader("Configuration")
        initial_capital = float(get_setting(conn, "initial_capital", str(DEFAULT_INITIAL_CAPITAL)))
        exchange = get_setting(conn, "exchange", DEFAULT_EXCHANGE)
        new_initial_capital = st.number_input("Capital initial (€)", min_value=0.0, value=initial_capital, step=1000.0)
        new_exchange = st.selectbox("Place de référence", ["XNYS", "XPAR", "XHKG", "XTKS"], index=["XNYS", "XPAR", "XHKG", "XTKS"].index(exchange if exchange in {"XNYS", "XPAR", "XHKG", "XTKS"} else DEFAULT_EXCHANGE))
        base_currency = st.selectbox("Devise de valorisation", ["EUR", "USD", "GBP", "JPY", "CHF"], index=["EUR", "USD", "GBP", "JPY", "CHF"].index(st.session_state["base_currency"]) if st.session_state["base_currency"] in {"EUR","USD","GBP","JPY","CHF"} else 0)
        benchmark_symbol = st.selectbox(
            "Indice comparatif",
            ["SPY", "EWJ", "EEM", "VGK", "QQQ", "VTI", "ACWI"],
            index=["SPY", "EWJ", "EEM", "VGK", "QQQ", "VTI", "ACWI"].index(st.session_state["benchmark_symbol"])
            if st.session_state["benchmark_symbol"] in {"SPY", "EWJ", "EEM", "VGK", "QQQ", "VTI", "ACWI"}
            else 0,
        )
        accounting_method = st.selectbox(
            "Méthode comptable",
            ["fifo", "lifo", "average"],
            format_func=lambda x: {"fifo": "FIFO", "lifo": "LIFO", "average": "Coût moyen pondéré"}.get(x, x),
            index=["fifo", "lifo", "average"].index(st.session_state["accounting_method"]) if st.session_state["accounting_method"] in {"fifo","lifo","average"} else 0,
        )
        if st.button("Enregistrer la configuration", use_container_width=True):
            set_setting(conn, "initial_capital", str(new_initial_capital))
            set_setting(conn, "exchange", new_exchange)
            set_setting(conn, "base_currency", base_currency)
            set_setting(conn, "benchmark_symbol", benchmark_symbol)
            set_setting(conn, "accounting_method", accounting_method)
            st.session_state["base_currency"] = base_currency
            st.session_state["benchmark_symbol"] = benchmark_symbol
            st.session_state["accounting_method"] = accounting_method
            st.success("Configuration enregistrée.")

        st.subheader("Mode par défaut")
        st.caption("Profil stable et prêt à l'emploi. Aucun changement n'est appliqué tant que vous ne cliquez pas.")
        if st.button("Appliquer le mode par défaut (stable)", use_container_width=True):
            preset = apply_default_mode_settings(conn, universe_symbols)
            st.session_state["live_enabled"] = preset.get("live_enabled", "1") == "1"
            st.session_state["live_mode"] = preset.get("live_mode", DEFAULT_LIVE_MODE)
            st.session_state["auto_refresh_enabled"] = preset.get("auto_refresh_enabled", "0") == "1"
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
            st.session_state["benchmark_symbol"] = preset.get("benchmark_symbol", DEFAULT_BENCHMARK_SYMBOL).upper()
            st.session_state["trade_slippage_bps"] = float(
                preset.get("trade_slippage_bps", str(DEFAULT_SIM_SLIPPAGE_BPS))
            )
            st.session_state["trade_spread_bps"] = float(
                preset.get("trade_spread_bps", str(DEFAULT_SIM_SPREAD_BPS))
            )
            st.session_state["backtest_fees_bps"] = float(preset.get("backtest_fees_bps", "8.0"))
            st.session_state["backtest_slippage_bps"] = float(preset.get("backtest_slippage_bps", "5.0"))
            fetch_quotes.clear()
            fetch_realtime_quotes.clear()
            fetch_polygon_snapshot_quotes.clear()
            st.success("Mode par défaut appliqué.")
            st.rerun()

        st.subheader("Flux Temps Réel")
        live_enabled = st.toggle("Activer cotation temps réel", value=st.session_state["live_enabled"])
        live_mode_label = st.radio(
            "Mode temps réel",
            ["Sondage multi-fournisseurs (Polygon/Yahoo)", "WebSocket tick-by-tick (Polygon)"],
            index=0 if st.session_state["live_mode"] == "polling" else 1,
        )
        live_mode = "polling" if live_mode_label.startswith("Sondage") else "websocket"
        auto_refresh_enabled = st.toggle(
            "Rafraîchissement automatique",
            value=st.session_state["auto_refresh_enabled"],
            help="Désactivez pour éviter les retours d’onglet et les rafraîchissements visuels.",
        )
        min_refresh = 1 if live_mode == "websocket" else 5
        refresh_seconds = st.slider(
            "Fréquence de rafraîchissement UI (secondes)",
            min_value=min_refresh,
            max_value=60,
            value=max(min_refresh, int(st.session_state["refresh_seconds"])),
        )
        realtime_symbols = st.multiselect(
            "Actifs sélectionnés (temps réel)",
            options=universe_symbols,
            default=[s for s in st.session_state["realtime_symbols"] if s in universe_symbols],
        )
        polygon_help = (
            "Utilise POLYGON_API_KEY ou colle la clé ici pour activer le flux tick-by-tick."
            if live_mode == "websocket"
            else "Optionnel en mode sondage: Polygon est utilisé en priorité pour les tickers US, puis Yahoo/secours."
        )
        polygon_api_key = st.text_input(
            "Clé API Polygon",
            value=st.session_state["polygon_api_key"],
            type="password",
            help=polygon_help,
        ).strip()
        st.session_state["polygon_api_key"] = polygon_api_key
        if live_mode == "websocket":
            st.caption("Le streaming Polygon tick-by-tick couvre surtout les tickers US (ex: AAPL, SPY, MSFT).")
        if not realtime_symbols:
            realtime_symbols = DEFAULT_REALTIME_SYMBOLS.copy()

        st.subheader("Qualité des instantanés")
        snapshot_min_seconds = st.slider(
            "Intervalle min instantanés (sec)",
            min_value=1,
            max_value=120,
            value=int(st.session_state["snapshot_min_seconds"]),
        )
        snapshot_min_delta = st.number_input(
            "Seuil variation min instantané",
            min_value=0.01,
            max_value=1000.0,
            value=float(st.session_state["snapshot_min_delta"]),
            step=0.5,
        )
        ws_stale_seconds = st.slider("Seuil obsolescence flux WS (sec)", min_value=5, max_value=120, value=int(st.session_state["ws_stale_seconds"]))

        st.subheader("Simulation exécution")
        trade_slippage_bps = st.number_input(
            "Glissement ordre (bps)",
            min_value=0.0,
            max_value=250.0,
            value=float(st.session_state["trade_slippage_bps"]),
            step=0.5,
        )
        trade_spread_bps = st.number_input(
            "Écart achat/vente ordre (bps)",
            min_value=0.0,
            max_value=250.0,
            value=float(st.session_state["trade_spread_bps"]),
            step=0.5,
        )

        st.subheader("Contraintes Risque")
        max_line_pct = st.slider("Max par ligne (%)", min_value=5.0, max_value=100.0, value=float(st.session_state["max_line_pct"]))
        max_sector_pct = st.slider("Max par secteur (%)", min_value=10.0, max_value=100.0, value=float(st.session_state["max_sector_pct"]))
        max_zone_pct = st.slider("Max par zone (%)", min_value=10.0, max_value=100.0, value=float(st.session_state["max_zone_pct"]))

        st.subheader("Paramètres simulation historique")
        backtest_fees_bps = st.number_input(
            "Frais simulation (bps)",
            min_value=0.0,
            max_value=300.0,
            value=float(st.session_state["backtest_fees_bps"]),
            step=1.0,
        )
        backtest_slippage_bps = st.number_input(
            "Glissement simulation (bps)",
            min_value=0.0,
            max_value=300.0,
            value=float(st.session_state["backtest_slippage_bps"]),
            step=1.0,
        )

        st.subheader("Alertes")
        alert_loss_pct = st.number_input("Alerte perte (%)", min_value=-100.0, max_value=0.0, value=float(st.session_state["alert_loss_pct"]), step=0.5)
        alert_drawdown_pct = st.number_input("Alerte repli maximal (%)", min_value=-100.0, max_value=0.0, value=float(st.session_state["alert_drawdown_pct"]), step=0.5)
        alert_gain_pct = st.number_input("Alerte gain (%)", min_value=0.0, max_value=500.0, value=float(st.session_state["alert_gain_pct"]), step=0.5)
        alert_webhook_url = st.text_input("Webhook alertes", value=st.session_state["alert_webhook_url"])
        alert_email_to = st.text_input("Email alertes", value=st.session_state["alert_email_to"])

        st.session_state["live_enabled"] = live_enabled
        st.session_state["auto_refresh_enabled"] = auto_refresh_enabled
        st.session_state["refresh_seconds"] = refresh_seconds
        st.session_state["realtime_symbols"] = realtime_symbols
        st.session_state["live_mode"] = live_mode
        st.session_state["snapshot_min_seconds"] = snapshot_min_seconds
        st.session_state["snapshot_min_delta"] = snapshot_min_delta
        st.session_state["ws_stale_seconds"] = ws_stale_seconds
        st.session_state["trade_slippage_bps"] = trade_slippage_bps
        st.session_state["trade_spread_bps"] = trade_spread_bps
        st.session_state["max_line_pct"] = max_line_pct
        st.session_state["max_sector_pct"] = max_sector_pct
        st.session_state["max_zone_pct"] = max_zone_pct
        st.session_state["backtest_fees_bps"] = backtest_fees_bps
        st.session_state["backtest_slippage_bps"] = backtest_slippage_bps
        st.session_state["alert_loss_pct"] = alert_loss_pct
        st.session_state["alert_drawdown_pct"] = alert_drawdown_pct
        st.session_state["alert_gain_pct"] = alert_gain_pct
        st.session_state["alert_webhook_url"] = alert_webhook_url
        st.session_state["alert_email_to"] = alert_email_to
        set_setting(conn, "live_enabled", "1" if live_enabled else "0")
        set_setting(conn, "auto_refresh_enabled", "1" if auto_refresh_enabled else "0")
        set_setting(conn, "refresh_seconds", str(refresh_seconds))
        set_setting(conn, "realtime_symbols", symbols_to_csv(realtime_symbols))
        set_setting(conn, "live_mode", live_mode)
        set_setting(conn, "snapshot_min_seconds", str(snapshot_min_seconds))
        set_setting(conn, "snapshot_min_delta", str(snapshot_min_delta))
        set_setting(conn, "ws_stale_seconds", str(ws_stale_seconds))
        set_setting(conn, "trade_slippage_bps", str(trade_slippage_bps))
        set_setting(conn, "trade_spread_bps", str(trade_spread_bps))
        set_setting(conn, "max_line_pct", str(max_line_pct))
        set_setting(conn, "max_sector_pct", str(max_sector_pct))
        set_setting(conn, "max_zone_pct", str(max_zone_pct))
        set_setting(conn, "backtest_fees_bps", str(backtest_fees_bps))
        set_setting(conn, "backtest_slippage_bps", str(backtest_slippage_bps))
        set_setting(conn, "alert_loss_pct", str(alert_loss_pct))
        set_setting(conn, "alert_drawdown_pct", str(alert_drawdown_pct))
        set_setting(conn, "alert_gain_pct", str(alert_gain_pct))
        set_setting(conn, "alert_webhook_url", alert_webhook_url.strip())
        set_setting(conn, "alert_email_to", alert_email_to.strip())
        st.caption(f"Les transactions et instantanés sont persistés dans `{db_path.as_posix()}`.")

    active_tab_hint = get_query_param_scalar("onglet").strip()
    allow_auto_refresh = active_tab_hint in AUTO_REFRESH_ALLOWED_TABS if active_tab_hint else True
    show_refresh_logo = False
    if (
        st.session_state["live_enabled"]
        and st.session_state["auto_refresh_enabled"]
        and allow_auto_refresh
        and st_autorefresh is not None
    ):
        refresh_count = st_autorefresh(interval=int(st.session_state["refresh_seconds"]) * 1000, key="portfolio-live-refresh")
        prev_count = int(st.session_state.get("last_autorefresh_count", -1))
        show_refresh_logo = refresh_count > 0 and refresh_count != prev_count
        st.session_state["last_autorefresh_count"] = int(refresh_count)
        if show_refresh_logo:
            log_event(conn, "INFO", "refresh_logo_tick", {"refresh_count": int(refresh_count)})
    render_refresh_logo_animation(show_refresh_logo)

    transactions = load_transactions(conn)
    positions_raw = compute_positions(transactions, accounting_method=st.session_state["accounting_method"])
    held_symbols = tuple(sorted(set(positions_raw["symbol"].tolist()))) if not positions_raw.empty else tuple()
    split_factors = fetch_split_factors(held_symbols, lookback_years=2) if held_symbols else {}
    positions = apply_split_adjustments_to_positions(positions_raw, split_factors) if not positions_raw.empty else positions_raw
    symbols_for_quotes = tuple(sorted(set(positions["symbol"].tolist() + st.session_state["realtime_symbols"])))
    trailing_dividends = fetch_trailing_dividends_per_share(tuple(sorted(set(positions["symbol"].tolist())))) if not positions.empty else {}
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
            "api_error",
        ]
    )
    stream_status = {"status": "disabled", "symbols": 0, "last_error": "", "last_message_utc": None, "last_tick_utc": None}
    polygon_stream = get_polygon_tick_stream()
    fallback_reason = ""
    ws_stale = False
    polygon_key = str(st.session_state.get("polygon_api_key", "")).strip()

    if not st.session_state["live_enabled"]:
        polygon_stream.stop()
    elif st.session_state["live_mode"] == "websocket":
        ws_symbols = [s for s in symbols_for_quotes if polygon_symbol_supported(s)]
        polygon_stream.configure(polygon_key, ws_symbols)
        ws_quotes = polygon_stream.quotes_df()
        stream_status = polygon_stream.status()
        ws_stale = polygon_stream.is_stale(st.session_state["ws_stale_seconds"])
        if ws_stale:
            fallback_reason = f"flux WS obsolète > {st.session_state['ws_stale_seconds']}s"
        missing = tuple([s for s in symbols_for_quotes if s not in set(ws_quotes.get("symbol", pd.Series(dtype=str)).tolist())])
        if ws_stale:
            missing = symbols_for_quotes
        polygon_missing = fetch_polygon_snapshot_quotes(missing, polygon_key) if missing and polygon_key else pd.DataFrame()
        polygon_ok = set()
        if not polygon_missing.empty:
            tmp = polygon_missing.copy()
            tmp["last"] = pd.to_numeric(tmp["last"], errors="coerce")
            polygon_ok = set(tmp.loc[tmp["last"] > 0, "symbol"].astype(str).str.upper().tolist())
            polygon_missing = polygon_missing[polygon_missing["symbol"].astype(str).str.upper().isin(polygon_ok)].copy()
        still_missing = tuple([s for s in missing if s not in polygon_ok])
        polled_backup = fetch_realtime_quotes(still_missing) if still_missing else pd.DataFrame()
        polled = merge_quotes(polygon_missing, polled_backup, missing)
        quotes = merge_quotes(ws_quotes, polled, symbols_for_quotes)
    else:
        polygon_stream.stop()
        polygon_first = fetch_polygon_snapshot_quotes(symbols_for_quotes, polygon_key) if polygon_key else pd.DataFrame()
        polygon_ok = set()
        if not polygon_first.empty:
            tmp = polygon_first.copy()
            tmp["last"] = pd.to_numeric(tmp["last"], errors="coerce")
            polygon_ok = set(tmp.loc[tmp["last"] > 0, "symbol"].astype(str).str.upper().tolist())
            polygon_first = polygon_first[polygon_first["symbol"].astype(str).str.upper().isin(polygon_ok)].copy()
        still_missing = tuple([s for s in symbols_for_quotes if s not in polygon_ok])
        yahoo_backup = fetch_realtime_quotes(still_missing) if still_missing else pd.DataFrame()
        quotes = merge_quotes(polygon_first, yahoo_backup, symbols_for_quotes)

    quotes = annotate_quote_freshness(quotes, stale_seconds=int(st.session_state["ws_stale_seconds"]))

    current_stream_signature = {
        "mode": st.session_state["live_mode"],
        "status": stream_status.get("status", "n/a"),
        "stale": ws_stale,
        "fallback": fallback_reason,
    }
    if st.session_state.get("last_stream_signature") != current_stream_signature:
        st.session_state["last_stream_signature"] = current_stream_signature
        log_event(conn, "INFO", "stream_status", current_stream_signature)

    error_cache = st.session_state.get("quote_error_cache", {})
    if not isinstance(error_cache, dict):
        error_cache = {}
    if quotes is not None and not quotes.empty and "api_error" in quotes.columns:
        latest_errors: dict[str, str] = {}
        for row in quotes.itertuples(index=False):
            symbol = str(getattr(row, "symbol", "")).upper()
            api_error = str(getattr(row, "api_error", "") or "").strip()
            if not symbol or not api_error:
                continue
            latest_errors[symbol] = api_error
            if error_cache.get(symbol) != api_error:
                log_event(
                    conn,
                    "WARNING",
                    "quote_symbol_error",
                    {
                        "symbol": symbol,
                        "source": str(getattr(row, "source", "")),
                        "api_error": api_error,
                        "market_state": str(getattr(row, "market_state", "")),
                    },
                )
        for symbol in [s for s in list(error_cache.keys()) if s not in latest_errors]:
            log_event(conn, "INFO", "quote_symbol_recovered", {"symbol": symbol})
        st.session_state["quote_error_cache"] = latest_errors

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
        trailing_dividends_per_share=trailing_dividends,
    )
    quote_freshness_note = quote_freshness_summary(quotes)
    provider_health_df = provider_health_table()

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

    tabs = st.tabs(MAIN_TAB_LABELS)
    target_focus = str(st.session_state.get("pending_tab_focus", "") or "").strip()
    if target_focus not in MAIN_TAB_LABELS:
        target_focus = ""
    url_tab = get_query_param_scalar("onglet").strip()
    if url_tab not in MAIN_TAB_LABELS:
        url_tab = ""
    if target_focus:
        force_active_tab(target_focus)
        try:
            st.query_params["onglet"] = target_focus
        except Exception:
            pass
    elif url_tab and url_tab != MAIN_TAB_LABELS[0]:
        force_active_tab(url_tab)
    sync_tabs_state(target_focus or url_tab or MAIN_TAB_LABELS[0])
    st.session_state["pending_tab_focus"] = ""

    with tabs[0]:
        status, market_subtitle, market_detail = create_market_clock_card(get_setting(conn, "exchange", DEFAULT_EXCHANGE))
        latest_quote = None
        if not quotes.empty and "quote_time_utc" in quotes.columns:
            latest_quote = pd.to_datetime(quotes["quote_time_utc"], errors="coerce", utc=True).max()
        if st.session_state["live_mode"] == "websocket":
            status_msg = str(stream_status.get("status", "n/a"))
            err = str(stream_status.get("last_error", "") or "")
            details = f"Mode: WebSocket Polygon ({localize_text_fr(status_msg)})"
            if err:
                details += f" | Erreur: {err}"
            if fallback_reason:
                details += f" | Secours REST: {fallback_reason}"
        else:
            details = "Mode: sondage multi-fournisseurs (Polygon -> Yahoo -> secours)"
        live_line = "Cotation en temps réel indisponible pour le moment."
        if latest_quote is not None and not pd.isna(latest_quote):
            live_line = f"Dernière mise à jour: {to_display_time(latest_quote.isoformat())}"
        st.caption(f"{details} | {live_line} | Devise portefeuille: {state['base_currency']}")
        st.caption(quote_freshness_note)
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
            st.metric("Instantanés sauvegardés", f"{len(snapshots)}")
            if fx_rates:
                st.caption("FX temps réel: " + ", ".join([f"1 {k} = {v:.4f} {state['base_currency']}" for k, v in fx_rates.items() if not np.isnan(v)]))

        st.plotly_chart(create_evolution_chart(snapshots, currency=state["base_currency"]), use_container_width=True)
        st.caption(f"Fraîcheur graphique évolution: {quote_freshness_note}")
        a1, a2 = st.columns(2)
        with a1:
            sector_alloc = holdings.groupby("secteur", as_index=False)["valeur_marche"].sum() if not holdings.empty else pd.DataFrame()
            st.plotly_chart(
                create_allocation_chart(sector_alloc, "secteur", "valeur_marche", "Répartition par secteur"),
                use_container_width=True,
            )
            st.caption(f"Fraîcheur répartition secteur: {quote_freshness_note}")
        with a2:
            geo_alloc = holdings.groupby("zone", as_index=False)["valeur_marche"].sum() if not holdings.empty else pd.DataFrame()
            st.plotly_chart(
                create_allocation_chart(geo_alloc, "zone", "valeur_marche", "Répartition par zone géographique"),
                use_container_width=True,
            )
            st.caption(f"Fraîcheur répartition zone: {quote_freshness_note}")

        p1, p2 = st.columns(2)
        with p1:
            st.plotly_chart(create_pnl_contribution_chart(holdings, transactions, state["base_currency"]), use_container_width=True)
            st.caption(f"Fraîcheur contribution gain/perte: {quote_freshness_note}")
        with p2:
            st.plotly_chart(create_drawdown_chart(snapshots, currency=state["base_currency"]), use_container_width=True)
            st.caption(f"Fraîcheur repli maximal: {quote_freshness_note}")

        pos_col, sell_col = st.columns([2.5, 1.5])
        with pos_col:
            render_positions_portefeuille(holdings, state["base_currency"], split_factors)
        with sell_col:
            render_quick_sell_panel(
                conn=conn,
                holdings=holdings,
                quotes=quotes,
                fx_rates=fx_rates,
                state=state,
                trade_slippage_bps=float(st.session_state["trade_slippage_bps"]),
                trade_spread_bps=float(st.session_state["trade_spread_bps"]),
                exchange_default=get_setting(conn, "exchange", DEFAULT_EXCHANGE),
                key_prefix="positions_quick_sell",
            )

    with tabs[1]:
        st.subheader("Univers d'actifs")
        region_tabs = st.tabs(["USA", "Europe", "Asie", "Pays émergent"])
        for region, region_tab in zip(["USA", "Europe", "Asie", "Pays émergent"], region_tabs):
            with region_tab:
                region_df = universe_df[universe_df["zone"] == region][["symbol", "name", "asset_type", "sector"]]
                render_dataframe_fr(region_df, use_container_width=True, hide_index=True)

        st.markdown("#### Métaux précieux et terres rares")
        metals_df = universe_df[universe_df["asset_type"].isin(["Métal précieux", "Terres rares"])][
            ["symbol", "name", "asset_type", "zone", "sector"]
        ]
        render_dataframe_fr(metals_df, use_container_width=True, hide_index=True)

        st.markdown("#### Prix unitaires temps réel des actifs sélectionnés")
        live_watch = quotes[quotes["symbol"].isin(st.session_state["realtime_symbols"])].copy() if not quotes.empty else pd.DataFrame()
        if live_watch.empty:
            st.info("Aucune cotation temps réel disponible pour la sélection actuelle.")
        else:
            live_watch["maj"] = live_watch["quote_time_utc"].apply(to_display_time)
            live_watch["variation_%"] = live_watch["change_pct"].round(2)
            live_watch["age_s"] = pd.to_numeric(live_watch.get("data_age_seconds"), errors="coerce").round(1)
            live_watch["stale_actif"] = live_watch.get("symbol_stale", False).map(lambda v: "Oui" if bool(v) else "Non")
            live_watch["sante_source"] = pd.to_numeric(live_watch.get("source_health_score"), errors="coerce").round(1)
            render_dataframe_fr(
                live_watch[
                    [
                        "symbol",
                        "last",
                        "previous",
                        "official_close",
                        "variation_%",
                        "market_state",
                        "price_context",
                        "currency",
                        "age_s",
                        "stale_actif",
                        "sante_source",
                        "maj",
                        "source",
                        "api_error",
                    ]
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
                        "age_s": "Âge tick (s)",
                        "stale_actif": "Actif obsolète",
                        "sante_source": "Santé du fournisseur",
                        "maj": "Dernière maj",
                        "source": "Source API",
                        "api_error": "Erreur API",
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
                order_type = st.selectbox("Type d'ordre", ["MARKET", "LIMIT", "STOP"], format_func=lambda x: localize_text_fr(x))
            with t4:
                quantity = st.number_input("Quantité", min_value=0.0, value=1.0, step=1.0)

            p1, p2, p3, p4 = st.columns([1.2, 1.2, 1, 1])
            with p1:
                market_quote = qmap.get(symbol, {})
                market_price = safe_float(market_quote.get("last", np.nan), np.nan)
                if np.isnan(market_price) or market_price <= 0:
                    one_shot = (
                        fetch_polygon_snapshot_quotes((symbol,), polygon_key)
                        if polygon_key and polygon_symbol_supported(symbol)
                        else pd.DataFrame()
                    )
                    if one_shot.empty:
                        one_shot = fetch_realtime_quotes((symbol,))
                    if not one_shot.empty:
                        market_quote = one_shot.iloc[0].to_dict()
                        market_price = safe_float(market_quote.get("last", np.nan), np.nan)
                market_currency = infer_currency(symbol, str(market_quote.get("currency", "")), state["base_currency"])
                price = float(market_price) if not np.isnan(market_price) else 0.0
                st.number_input(
                    "Prix unitaire (marché)",
                    min_value=0.0,
                    value=float(price if price > 0 else 0.0),
                    step=0.01,
                    disabled=True,
                    help="Prix injecté automatiquement depuis l'API marché (non modifiable).",
                )
            with p2:
                trigger_price = st.number_input(
                    "Prix déclenchement",
                    min_value=0.0,
                    value=float(price if order_type != "MARKET" and price > 0 else 0.0),
                    step=0.01,
                    disabled=(order_type == "MARKET"),
                    help="Utilisé pour les ordres Limite et Stop.",
                )
            with p3:
                slippage_bps = st.number_input(
                    "Glissement (bps)",
                    min_value=0.0,
                    value=float(st.session_state["trade_slippage_bps"]),
                    step=0.5,
                )
            with p4:
                spread_bps = st.number_input(
                    "Écart achat/vente (bps)",
                    min_value=0.0,
                    value=float(st.session_state["trade_spread_bps"]),
                    step=0.5,
                )
            x1, x2, x3 = st.columns([1, 1, 2])
            with x1:
                fees = st.number_input("Frais", min_value=0.0, value=0.0, step=0.01)
            with x2:
                trade_exchange = st.selectbox("Marché", ["XNYS", "XPAR", "XHKG", "XTKS"], index=0)
            with x3:
                note = st.text_input("Note (optionnelle)")
            strategy_tag = st.text_input("Tag stratégie", value="manuel")
            if price > 0:
                quote_ctx = localize_text_fr(str(market_quote.get("price_context", "unknown")))
                quote_src = str(market_quote.get("source", "unknown"))
                quote_at = to_display_time(str(market_quote.get("quote_time_utc", "")))
                st.caption(f"Cotation utilisée: {price:.4f} {market_currency} | contexte: {quote_ctx} | source: {quote_src} | maj: {quote_at}")
            else:
                st.caption("Cotation indisponible actuellement pour cet actif.")
            submitted = st.form_submit_button("Enregistrer la transaction", use_container_width=True)
            if submitted:
                quote_currency = market_currency
                fx_to_base = safe_float(fx_rates.get(quote_currency, np.nan), np.nan)
                if np.isnan(fx_to_base) or fx_to_base <= 0:
                    fx_to_base = 1.0 if quote_currency == state["base_currency"] else safe_float(market_quote.get("fx_to_base", 1.0), 1.0)
                if quantity <= 0:
                    st.error("La quantité doit être positive.")
                elif price <= 0:
                    st.error("Prix de marché indisponible. Impossible d'exécuter l'ordre pour éviter un prix incohérent.")
                elif side == "SELL" and quantity > float(held_qty.get(symbol, 0.0)):
                    st.error("Quantité vendue supérieure à la position détenue.")
                else:
                    execution = simulate_order_execution(
                        side=side,
                        order_type=order_type,
                        market_price=float(price),
                        quantity=float(quantity),
                        trigger_price=(float(trigger_price) if order_type != "MARKET" else None),
                        slippage_bps=float(slippage_bps),
                        spread_bps=float(spread_bps),
                        symbol=symbol,
                    )
                    exec_status = str(execution.get("execution_status", "PENDING")).upper()
                    exec_qty = float(execution.get("executed_quantity", 0.0))
                    exec_price = float(execution.get("executed_price", price))
                    fill_ratio = float(execution.get("fill_ratio", 0.0))
                    fees_exec = float(fees) * fill_ratio
                    risk_errors: list[str] = []
                    if exec_status in {"FILLED", "PARTIAL"}:
                        risk_errors = check_trade_risk(
                            side=side,
                            symbol=symbol,
                            quantity=float(exec_qty),
                            price=float(exec_price),
                            fees=float(fees_exec),
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
                            fees=float(fees_exec),
                            currency=quote_currency,
                            fx_to_base=float(fx_to_base),
                            exchange=trade_exchange,
                            strategy_tag=strategy_tag,
                            note=note,
                            order_type=order_type,
                            trigger_price=float(trigger_price) if order_type != "MARKET" else None,
                            execution_status=exec_status,
                            fill_ratio=fill_ratio,
                            executed_quantity=exec_qty,
                            executed_price=exec_price,
                            slippage_bps=float(slippage_bps),
                            spread_bps=float(spread_bps),
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
                                "order_type": order_type,
                                "execution_status": exec_status,
                                "executed_qty": exec_qty,
                                "executed_price": exec_price,
                                "currency": quote_currency,
                                "fx_to_base": float(fx_to_base),
                            },
                        )
                        if exec_status in {"FILLED", "PARTIAL"}:
                            label = (
                                f"{'Achat' if side == 'BUY' else 'Vente'} {exec_qty:g}/{quantity:g} {symbol} "
                                f"@ {exec_price:.2f} {quote_currency} ({localize_text_fr(exec_status)})"
                            )
                            st.session_state["pending_snapshot_event"] = {"type": side, "label": label}
                        else:
                            st.session_state["pending_snapshot_event"] = None
                        fetch_quotes.clear()
                        fetch_realtime_quotes.clear()
                        fetch_polygon_snapshot_quotes.clear()
                        if exec_status == "PENDING":
                            st.warning("Ordre enregistré en statut En attente (condition de prix non déclenchée).")
                        else:
                            st.success(f"Transaction enregistrée ({localize_text_fr(exec_status)}).")
                        st.rerun()

        render_positions_portefeuille(holdings, state["base_currency"], split_factors)

    with tabs[2]:
        st.subheader("Analyse des marchés")
        selected = st.session_state["realtime_symbols"]
        st.caption("La sélection des actifs temps réel se pilote dans la barre latérale.")
        st.caption(quote_freshness_note)
        if st.session_state["live_mode"] == "websocket":
            st.caption(
                f"Flux WebSocket Polygon: {localize_text_fr(stream_status.get('status', 'n/a'))} | "
                f"Tickers streamés: {stream_status.get('symbols', 0)}"
            )
        market_quotes = quotes[quotes["symbol"].isin(selected)].copy() if not quotes.empty else pd.DataFrame()
        if market_quotes.empty:
            st.warning("Impossible de charger les cotations en temps réel pour le moment.")
        else:
            table = market_quotes.copy()
            table["variation_%"] = table["change_pct"].round(2)
            table["age_s"] = pd.to_numeric(table.get("data_age_seconds"), errors="coerce").round(1)
            table["stale_actif"] = table.get("symbol_stale", False).map(lambda v: "Oui" if bool(v) else "Non")
            table["sante_source"] = pd.to_numeric(table.get("source_health_score"), errors="coerce").round(1)
            render_dataframe_fr(
                table[
                    [
                        "symbol",
                        "last",
                        "previous",
                        "official_close",
                        "variation_%",
                        "market_state",
                        "price_context",
                        "currency",
                        "age_s",
                        "stale_actif",
                        "sante_source",
                        "source",
                        "api_error",
                    ]
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
                        "age_s": "Âge tick (s)",
                        "stale_actif": "Actif obsolète",
                        "sante_source": "Santé du fournisseur",
                        "source": "Source API",
                        "api_error": "Erreur API",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### Santé des fournisseurs")
        if provider_health_df.empty:
            st.caption("Santé des fournisseurs indisponible.")
        else:
            render_dataframe_fr(
                provider_health_df.sort_values("score", ascending=False).rename(
                    columns={
                        "provider": "Fournisseur",
                        "score": "Score santé",
                        "success": "Succès",
                        "error": "Erreurs",
                        "consecutive_error": "Erreurs consécutives",
                        "circuit_open": "Circuit ouvert",
                        "last_error": "Dernière erreur",
                        "last_error_utc": "Horodatage erreur",
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
                render_dataframe_fr(
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
                render_dataframe_fr(
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
        st.subheader("Simulation & Opérations")
        st.caption("Simulation historique, relecture des instantanés, alertes et logs techniques.")
        with st.expander("À quoi sert cet onglet ? (version simple)", expanded=False):
            st.markdown(
                """
                Cet onglet sert à tester et piloter ton portefeuille sans toucher au réel.

                - `Simulation historique (backtest)`: simule ce qui se serait passé dans le passé avec une stratégie (ex: achat long terme, stratégie SMA50).
                - `Relecture des instantanés`: rejoue l'historique de la valeur de ton portefeuille comme une timeline.
                - `Alertes récentes`: liste les avertissements automatiques (perte, repli maximal, concentration).
                - `Logs techniques`: journal interne utile pour comprendre ce qui s'est passé dans l'application.
                """
            )

        bt1, bt2, bt3, bt4 = st.columns(4)
        with bt1:
            bt_strategy = st.selectbox("Stratégie", ["buy_hold", "sma50"], format_func=lambda x: "Achat & conservation équipondéré" if x == "buy_hold" else "SMA50 dynamique")
        with bt2:
            bt_start = st.date_input("Début", value=pd.Timestamp.now(tz="UTC").date() - pd.Timedelta(days=365 * 3))
        with bt3:
            bt_end = st.date_input("Fin", value=pd.Timestamp.now(tz="UTC").date())
        with bt4:
            bt_benchmark = st.selectbox(
                "Indice de référence",
                ["SPY", "EWJ", "EEM", "VGK", "QQQ", "VTI", "ACWI"],
                index=["SPY", "EWJ", "EEM", "VGK", "QQQ", "VTI", "ACWI"].index(st.session_state["benchmark_symbol"])
                if st.session_state["benchmark_symbol"] in {"SPY", "EWJ", "EEM", "VGK", "QQQ", "VTI", "ACWI"}
                else 0,
            )
        bt_symbols = st.multiselect("Actifs de simulation", options=universe_symbols, default=st.session_state["realtime_symbols"])
        b2, b3, b4 = st.columns(3)
        with b2:
            bt_capital = st.number_input("Capital initial de simulation", min_value=1.0, value=float(state["initial_capital"]), step=1000.0)
        with b3:
            bt_exchange = st.selectbox(
                "Calendrier marché",
                ["XNYS", "XPAR", "XTKS", "XHKG"],
                index=["XNYS", "XPAR", "XTKS", "XHKG"].index(get_setting(conn, "exchange", DEFAULT_EXCHANGE))
                if get_setting(conn, "exchange", DEFAULT_EXCHANGE) in {"XNYS", "XPAR", "XTKS", "XHKG"}
                else 0,
            )
        with b4:
            st.caption(
                f"Coûts intégrés: frais {float(st.session_state['backtest_fees_bps']):.1f} bps "
                f"+ glissement {float(st.session_state['backtest_slippage_bps']):.1f} bps"
            )
        if st.button("Lancer la simulation", use_container_width=True):
            if bt_start >= bt_end:
                st.error("La date de début doit être antérieure à la date de fin.")
            else:
                curve, metrics = run_backtest(
                    symbols=bt_symbols or st.session_state["realtime_symbols"],
                    start=str(bt_start),
                    end=str(bt_end),
                    initial_capital=float(bt_capital),
                    strategy=bt_strategy,
                    exchange=bt_exchange,
                    benchmark_symbol=bt_benchmark,
                    fees_bps=float(st.session_state["backtest_fees_bps"]),
                    slippage_bps=float(st.session_state["backtest_slippage_bps"]),
                )
                if curve.empty:
                    st.error("Simulation indisponible (données manquantes).")
                else:
                    curve_records = dataframe_records_json_safe(curve)
                    cursor = conn.execute(
                        """
                        INSERT INTO backtest_runs(
                            created_at_utc, strategy, symbols_csv, start_date, end_date, initial_capital, metrics_json, curve_json, benchmark
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            utc_now_iso(),
                            bt_strategy,
                            symbols_to_csv(bt_symbols or st.session_state["realtime_symbols"]),
                            str(bt_start),
                            str(bt_end),
                            float(bt_capital),
                            json.dumps(metrics, ensure_ascii=False),
                            json.dumps(curve_records, ensure_ascii=False),
                            bt_benchmark,
                        ),
                    )
                    conn.commit()
                    run_id = int(cursor.lastrowid)
                    st.session_state["benchmark_symbol"] = bt_benchmark
                    set_setting(conn, "benchmark_symbol", bt_benchmark)
                    log_event(
                        conn,
                        "INFO",
                        "backtest_run",
                        {"id": run_id, "strategy": bt_strategy, "symbols": bt_symbols, "benchmark": bt_benchmark},
                    )
                    st.success("Simulation enregistrée.")
                    st.session_state["backtest_result"] = {
                        "id": run_id,
                        "strategy": bt_strategy,
                        "start": str(bt_start),
                        "end": str(bt_end),
                        "symbols_csv": symbols_to_csv(bt_symbols or st.session_state["realtime_symbols"]),
                        "initial_capital": float(bt_capital),
                        "metrics": metrics,
                        "curve": curve_records,
                        "benchmark": bt_benchmark,
                        "exchange": bt_exchange,
                        "currency": state["base_currency"],
                        "created_at": utc_now_iso(),
                    }
                    st.session_state["pending_tab_focus"] = "Simulation & Opérations"
                    st.rerun()

        backtest_result = st.session_state.get("backtest_result")
        if backtest_result:
            st.markdown("<div class='backtest-panel'>", unsafe_allow_html=True)
            bt_title_col, bt_close_col = st.columns([20, 1])
            with bt_title_col:
                st.markdown("<div class='backtest-panel-title'>Résultat de simulation (persistant)</div>", unsafe_allow_html=True)
                st.caption(
                    f"Exécution: `#{backtest_result.get('id', '')}` | "
                    f"Stratégie: `{backtest_result.get('strategy', '')}` | "
                    f"Période: {backtest_result.get('start', '')} -> {backtest_result.get('end', '')} | "
                    f"Actifs: {backtest_result.get('symbols_csv', '')} | "
                    f"Indice de référence: {backtest_result.get('benchmark', '')} | "
                    f"Place: {backtest_result.get('exchange', '')}"
                )
            with bt_close_col:
                if st.button("✕", key="close_backtest_panel", help="Fermer le résultat de la simulation"):
                    st.session_state["backtest_result"] = None
                    st.session_state["pending_tab_focus"] = "Simulation & Opérations"
                    st.rerun()

            bt_metrics = backtest_result.get("metrics", {})
            render_dataframe_fr(pd.DataFrame([bt_metrics]), use_container_width=True, hide_index=True)
            bt_curve_raw = backtest_result.get("curve", [])
            bt_curve_df = pd.DataFrame(bt_curve_raw)
            if not bt_curve_df.empty and "date" in bt_curve_df.columns:
                bt_curve_df["date"] = pd.to_datetime(bt_curve_df["date"], errors="coerce")
                bt_curve_df = bt_curve_df.dropna(subset=["date"])
            if not bt_curve_df.empty and {"equity", "drawdown"}.issubset(set(bt_curve_df.columns)):
                fig_bt = go.Figure()
                fig_bt.add_trace(
                    go.Scatter(
                        x=bt_curve_df["date"],
                        y=bt_curve_df["equity"],
                        mode="lines",
                        line={"color": "#103b88", "width": 2.6},
                        name="Capital",
                    )
                )
                dd_series = pd.to_numeric(bt_curve_df["drawdown"], errors="coerce") * 100
                fig_bt.add_trace(
                    go.Scatter(
                        x=bt_curve_df["date"],
                        y=dd_series,
                        mode="lines",
                        line={"color": "#d93025", "width": 2.0},
                        fill="tozeroy",
                        fillcolor="rgba(217,48,37,0.18)",
                        name="Repli maximal %",
                        yaxis="y2",
                    )
                )
                recovery_dd = dd_series.where(dd_series > -1.0, np.nan)
                if not recovery_dd.dropna().empty:
                    fig_bt.add_trace(
                        go.Scatter(
                            x=bt_curve_df["date"],
                            y=recovery_dd,
                            mode="lines",
                            line={"color": "#0f9d58", "width": 1.8},
                            name="Zone de récupération",
                            yaxis="y2",
                        )
                    )
                apply_plot_theme(
                    fig_bt,
                    title="Backtest - Courbe de capital et repli maximal",
                    xaxis_title="Date",
                    yaxis_title=f"Capital ({backtest_result.get('currency', state['base_currency'])})",
                    margin_top=52,
                )
                fig_bt.update_layout(
                    yaxis2={
                        "overlaying": "y",
                        "side": "right",
                        "title": "Repli maximal %",
                        "showgrid": False,
                        "zeroline": False,
                        "linecolor": "#bfd0ea",
                        "tickfont": {"color": "#102a5c"},
                    }
                )
                fig_bt.update_xaxes(tickformat="%b %Y", tickangle=0, nticks=8)
                st.plotly_chart(fig_bt, use_container_width=True)
                st.plotly_chart(create_benchmark_relative_chart(bt_curve_df), use_container_width=True)
                st.caption(f"Fraîcheur vues simulation: {quote_freshness_note}")
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("#### Simulations sauvegardées et comparaison")
        runs_df = load_backtest_runs(conn, limit=30)
        if runs_df.empty:
            st.caption("Aucune simulation sauvegardée.")
        else:
            display_cols = [
                c
                for c in ["id", "created_at_utc", "strategy", "benchmark", "start_date", "end_date", "annual_return_pct", "max_drawdown_pct", "final_value"]
                if c in runs_df.columns
            ]
            if display_cols:
                render_dataframe_fr(runs_df[display_cols], use_container_width=True, hide_index=True)
            options = runs_df["id"].astype(int).tolist()
            compare_default = options[: min(2, len(options))]
            compare_ids = st.multiselect("Comparer les simulations", options=options, default=compare_default)
            if compare_ids:
                fig_cmp = go.Figure()
                for run_id in compare_ids:
                    row = runs_df[runs_df["id"] == run_id].iloc[0]
                    curve_df = parse_curve_json(str(row.get("curve_json", "")))
                    if curve_df.empty or "equity" not in curve_df.columns:
                        continue
                    eq = pd.to_numeric(curve_df["equity"], errors="coerce")
                    dates = pd.to_datetime(curve_df["date"], errors="coerce") if "date" in curve_df.columns else None
                    eq = eq.dropna()
                    if eq.empty:
                        continue
                    base = float(eq.iloc[0]) if float(eq.iloc[0]) != 0 else 1.0
                    if dates is not None:
                        y = pd.to_numeric(curve_df["equity"], errors="coerce") / base * 100.0
                        fig_cmp.add_trace(
                            go.Scatter(
                                x=dates,
                                y=y,
                                mode="lines",
                                name=f"Exécution #{int(run_id)} - {localize_text_fr(row.get('strategy', ''))}",
                            )
                        )
                fig_cmp.update_layout(
                    title="Comparaison visuelle des simulations (Base 100)",
                    template=None,
                    paper_bgcolor="#ffffff",
                    plot_bgcolor="#ffffff",
                    font={"color": "#102a5c"},
                    xaxis_title="Date",
                    yaxis_title="Performance (base 100)",
                    margin={"l": 70, "r": 24, "t": 52, "b": 48},
                    legend={"bgcolor": "rgba(255,255,255,0.92)", "bordercolor": "#d7e3f8", "borderwidth": 1},
                )
                fig_cmp.update_xaxes(showgrid=True, gridcolor="#dbe5f5", linecolor="#bfd0ea", tickfont={"color": "#102a5c"})
                fig_cmp.update_yaxes(showgrid=True, gridcolor="#dbe5f5", linecolor="#bfd0ea", tickfont={"color": "#102a5c"})
                st.plotly_chart(fig_cmp, use_container_width=True)

        st.markdown("#### Rebalancement assisté")
        st.caption("Propose des ordres de réduction/renforcement selon les contraintes ligne/secteur/zone.")
        rebalance_plan = build_rebalance_plan(
            holdings=holdings,
            state=state,
            max_line_pct=float(st.session_state["max_line_pct"]),
            max_sector_pct=float(st.session_state["max_sector_pct"]),
            max_zone_pct=float(st.session_state["max_zone_pct"]),
        )
        if rebalance_plan.empty:
            st.info("Aucune action de rebalancement prioritaire détectée.")
        else:
            rebal_view = rebalance_plan.copy()
            rebal_view["notional_base"] = pd.to_numeric(rebal_view["notional_base"], errors="coerce").round(2)
            render_dataframe_fr(
                rebal_view.rename(
                    columns={
                        "action": "Action",
                        "symbol": "Actif",
                        "notional_base": f"Montant ({state['base_currency']})",
                        "reason": "Raison",
                        "priority": "Priorité",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### Relecture des instantanés")
        snapshot_count = len(snapshots) if isinstance(snapshots, pd.DataFrame) else 0
        if snapshot_count <= 0:
            st.info("Aucun instantané à rejouer.")
        else:
            max_idx = max(snapshot_count - 1, 0)
            replay_key = "replay_idx_snapshot"
            prev_idx = st.session_state.get(replay_key, max_idx)
            try:
                prev_idx_int = int(prev_idx)
            except Exception:
                prev_idx_int = max_idx
            prev_idx_int = max(0, min(prev_idx_int, max_idx))
            st.session_state[replay_key] = prev_idx_int
            replay_idx = st.slider(
                "Point dans l'historique",
                min_value=0,
                max_value=max_idx,
                value=prev_idx_int,
                key=replay_key,
            )
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
            render_dataframe_fr(alerts_df[["created_at_utc", "severity", "title", "message", "delivered"]], use_container_width=True, hide_index=True)

        st.markdown("#### Logs techniques")
        logs_df = load_recent_logs(conn, limit=60)
        if logs_df.empty:
            st.caption("Aucun log applicatif.")
        else:
            render_dataframe_fr(logs_df, use_container_width=True, hide_index=True)

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
            render_dataframe_fr(pd.DataFrame(st.session_state["last_structured_recs"]), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
