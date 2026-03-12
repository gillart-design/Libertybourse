# Outil de gestion de portefeuille boursier (clé en main)

Outil Python complet pour analyser et optimiser un portefeuille avec:

- Ratio de Sharpe
- Théorie du portefeuille de Markowitz (frontière efficiente + portefeuille max Sharpe)
- Courbe de volatilité avec formule de variance empirique
- Cycles boursiers (bull/bear via moyennes mobiles)
- Proxy de cycles de Kondratiev (heuristique long terme)
- Portefeuilles pertinents additionnels: minimum variance, risk parity, equal weight
- Métriques supplémentaires: Sortino, VaR/CVaR, Max Drawdown, Beta benchmark, corrélations
- Sauvegardes automatiques horodatées des sorties
- Source de données API (Yahoo Finance via `yfinance`) + prise en compte des horaires de marché

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Utilisation rapide

### 1) Mode démo (données synthétiques)

```bash
python main.py
```

### 2) Avec tes données CSV

```bash
python main.py --csv data/prices.csv --risk-free 0.03 --benchmark SP500
```

### 3) Avec API boursière (Yahoo Finance)

```bash
python main.py \
  --tickers AAPL,MSFT,GOOGL,SPY \
  --benchmark SPY \
  --start 2015-01-01 \
  --exchange XNYS \
  --risk-free 0.03
```

Notes API:
- Le filtrage des sessions de marché est activé par défaut.
- Pour le désactiver: `--ignore-market-hours`.

## Format CSV accepté

### Format large

```csv
Date,AAPL,MSFT,GOOGL,SP500
2020-01-02,75.09,160.62,68.43,3257.85
2020-01-03,74.36,158.62,67.79,3234.85
```

### Format long

```csv
Date,Ticker,Close
2020-01-02,AAPL,75.09
2020-01-02,MSFT,160.62
2020-01-03,AAPL,74.36
```

## Sorties générées (`output/`)

- `portfolio_report.json`: rapport global (poids, métriques, cycles)
- `optimal_weights.csv`: pondérations de tous les portefeuilles
- `efficient_frontier.csv`: points de la frontière efficiente
- `portfolio_returns.csv`: série de rendements de chaque portefeuille
- `efficient_frontier.png`
- `correlation_heatmap.png`
- `portfolios_comparison.png`
- `market_cycles.png`
- `dominant_cycles.png`
- `portfolio_<nom>.png`: 1 graphique complet par portefeuille créé
- `backups/<timestamp>/...`: sauvegarde horodatée des artefacts (désactivable avec `--no-backup`)

## Portefeuilles générés automatiquement

- `max_sharpe`: portefeuille tangent (Markowitz + Sharpe)
- `minimum_variance`: risque minimal
- `risk_parity`: contribution au risque équilibrée
- `equal_weight`: allocation naïve équipondérée

## Options CLI utiles

- `--tickers`: liste de symboles pour mode API
- `--start` / `--end`: bornes temporelles mode API
- `--interval`: granularité (`1d`, `1h`, etc.)
- `--exchange`: calendrier de marché (`XNYS`, `XPAR`, ...)
- `--ignore-market-hours`: ignore les horaires/sessions officielles
- `--backup-dir`: emplacement backup (par défaut `output/backups`)
- `--no-backup`: pas de backup automatique

## Notes importantes

- Le module Kondratiev est un proxy quantitatif (non prédictif) et nécessite idéalement > 40 ans d'historique mensuel.
- Les résultats dépendent fortement de la qualité des données et des hypothèses (stationnarité, liquidité, coûts de transaction absents ici).
- Pour production réelle, ajoute des contraintes métiers (poids max par actif, turnover, frais, taxes, slippage).

## Simulateur web multi-onglets

Une application Streamlit est disponible pour un usage interactif avec:

- URL de production: https://libertybourse-kbussevqm2stdwmrqbxkf4.streamlit.app

- Synthèse KPI + graphique d'évolution avec snapshots achat/vente/hausse/baisse
- Actualisation temps réel des actifs sélectionnés (Yahoo Quote API + rafraîchissement périodique)
- Mode WebSocket tick-by-tick (Polygon) pour les tickers US avec fallback REST automatique
- Bascule auto WS -> REST si flux stale, puis retour automatique au streaming
- Valorisation multi-devises avec conversion FX live vers devise de base
- Comptabilité de positions configurable (FIFO / LIFO / Average) avec PnL réalisé
- Contrôles anti-explosion snapshots (intervalle min + variation min)
- Alertes seuils (perte, gain, drawdown, concentration) + webhook/email
- Distinction des contextes de prix (regular, pre, post, close officiel)
- Garde-fous de risque avant transaction (cash, ligne, secteur, zone)
- Backtest intégré (Buy&Hold, SMA50) + replay snapshots
- Assistant IA avec plan d'actions structuré (action, taille, confiance, invalidation)
- Logs applicatifs + tests unitaires `pytest`
- Répartition par secteur et par zone géographique
- Sélection d'actifs par région (USA, Europe, Asie, Pays émergent)
- Liste dédiée métaux précieux et terres rares
- Onglet Marchés (cotations ETF/Actions + opportunités + vigilance + contexte géopolitique)
- Onglet Assistant d'aide à la décision (moteur local + option OpenAI via `OPENAI_API_KEY`)

Lancement:

```bash
streamlit run portfolio_simulator_app.py
```

Option WebSocket Polygon:

- Définis `POLYGON_API_KEY` (ou saisis la clé dans la sidebar).
- Active `Mode live -> WebSocket tick-by-tick (Polygon)`.
- Le flux utilise des ticks US; les actifs non streamables restent couverts via REST.

Persistance:

- Base SQLite: `data/portfolio_simulator.db`
- Sauvegarde réactive des transactions et des snapshots de valorisation.
- Les paramètres live (actifs suivis + fréquence) sont conservés dans `settings`.

Tests:

```bash
pytest -q
```
