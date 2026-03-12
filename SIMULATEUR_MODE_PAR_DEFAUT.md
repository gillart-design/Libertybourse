# Mode Par Defaut - Simulateur Boursier

Ce document decrit le profil "mode par defaut (stable)" integre dans le simulateur.

Objectif:
- Fournir un preset cle en main, utilisable immediatement par n'importe quel utilisateur.
- Ne rien changer automatiquement a la configuration en cours.
- Appliquer les options uniquement sur action explicite utilisateur.

## Ou le trouver

Dans la barre laterale, section:
- `Mode par defaut`
- Bouton: `Appliquer le mode par defaut (stable)`

## Comportement

- Tant que le bouton n'est pas clique: aucune configuration existante n'est modifiee.
- Au clic: le profil stable est applique aux options operationnelles live/risque/snapshots/alertes.
- Les parametres "Configuration" (capital initial, exchange, devise, methode comptable) ne sont pas forces par ce preset.

## Valeurs appliquees par le mode par defaut

- `live_enabled = 1`
- `live_mode = polling` (Yahoo REST, sans cle API obligatoire)
- `refresh_seconds = 10`
- `realtime_symbols = SPY,QQQ,AAPL,MSFT,GLD,EEM` (si disponibles)
- `snapshot_min_seconds = 10`
- `snapshot_min_delta = 1.0`
- `ws_stale_seconds = 20`
- `max_line_pct = 25.0`
- `max_sector_pct = 45.0`
- `max_zone_pct = 55.0`
- `alert_loss_pct = -7.0`
- `alert_drawdown_pct = -10.0`
- `alert_gain_pct = 10.0`

## Pourquoi ce preset est stable

- Mode live REST par defaut (pas de dependance a une cle WebSocket).
- Refresh modere pour limiter la charge et les erreurs reseau.
- Seuils de snapshots anti-bruit pour garder une courbe lisible.
- Limites de risque et alertes pre-configurees.

