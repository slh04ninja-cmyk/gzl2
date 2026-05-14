# Context.md — TradingBot GZL2

> **Dernière mise à jour :** 2026-05-14 (v4.6.0)
> **Commande `/maj`** : mettre à jour ce fichier avec les derniers changements du projet.

## 📋 Résumé du projet
Bot de copy trading Telegram → MetaTrader 5 (Exness). Écoute des canaux Telegram de signaux trading gold, parse les signaux en temps réel et exécute les ordres automatiquement sur MT5.

## 🏗️ Architecture
```
gzl2/
├── telegram_listener_v4.py   # Bot principal (écoute TG → exécute MT5)
├── signal_parser.py           # Parser de signaux V5.1 (importé par le bot)
├── supabase_logger.py         # Logger Supabase (sessions, trades, events)
├── dashboard.py               # Dashboard Streamlit (visualisation performances)
├── bot.env                    # Config fixe (canaux, lots, filtres)
├── .env                       # Secrets (non commité)
├── requirements.txt           # Dépendances Python
├── supabase_schema.sql        # Schéma DB Supabase
├── HTF_Gold_EA_v*.mq5         # Expert Advisor MT5 (diverses versions)
├── HTF_Gold_EA_v*.set          # Fichiers de paramètres EA
├── start_bot.bat              # Script de lancement Windows
└── .github/workflows/         # Déploiement RDP via GitHub Actions
    ├── rdp-tailscale-bot-v4.yml      # Workflow principal (RDP + Tailscale + Bot)
    ├── rdp-tailscale-stop.yml        # Nettoyage devices Tailscale
    ├── rdp-tailscale-rustdesk-A.yml  # RustDesk variant A
    ├── rdp-tailscale-rustdesk-B.yml  # RustDesk variant B
    └── rdp-tailscale-bot-A-1.yml     # Bot variant A
```

## 🔧 Technologies
- **Telegram** : Telethon 1.36.0 (compte utilisateur)
- **Trading** : MetaTrader5 (Python lib) → broker Exness
- **Base de données** : Supabase (PostgreSQL)
- **Dashboard** : Streamlit + Plotly
- **Déploiement** : GitHub Actions → Windows RDP (Tailscale + RustDesk)
- **Python** : 3.11+

## 📡 Canaux Telegram surveillés
Configurés dans `bot.env` (TG_CHANNEL_1 à TG_CHANNEL_6) :
- `@fxGzl`
- 5 canaux numériques (IDs négatifs)

## 🔍 Parser de signaux V5.1 (signal_parser.py)

### Types de signaux
| Type | Description |
|---|---|
| `TRADE` | Signal d'entrée BUY/SELL |
| `CLOSE` | Fermer position (`close all`, `close XAUUSD`) |
| `SL_MOVE` | Déplacer SL (`SL MOVE 4650`, `New SL: 4650`) |

### Entry (extraction prix)
1. Range : `4630/4625`, `4630-4625` → midpoint
2. Labelé : `ENTRY: 3240`, `OPEN: 3240`, `@ 3240`, `ZONE: 3240`
3. Inline : `BUY 3240`, `SELL 3240` (sans range)
4. Fallback : mini-zone ±0.5 autour du prix

### TP supportés (10 patterns)
1. `TPn: prix` — `TP1: 4628`
2. `TP.n: prix` — `TP.1: 3245`
3. `TPn (prix)` — `TP1: (3245)`
4. `TAKE PROFIT n prix` — `TAKE PROFIT 1: 4655`
5. `TAKE PROFIT WORD prix` — `TAKE PROFIT ONE 4650`
6. `✅ TPn: prix` / `✅TPⁿ prix` — emoji checkmark (avec ou sans espace)
7. `TPⁿ prix` — superscript Unicode (TP¹, TP², TPⁿ)
8. `TARGET n prix` / `TGT n prix`
9. `TP prix` — ligne seule sans numéro
10. `TP. ¹ prix` — point + superscript

### SL supportés (10 patterns)
1. `Stop Loss (SL): prix`
2. `STOP LOSS. prix`
3. `SL BREAKOUT prix`
4. `SL: prix` (standard)
5. `SL_prix` (underscore)
6. `SL-prix` (tiret)
7. `SL. prix` (point)
8. `(SL): prix` (parenthèses)
9. `STOP: prix`
10. `🛑 SL prix` (emoji)

### Symboles
- `XAUUSD`, `GOLD`, `XAU/USD` → XAUUSD
- `XAGUSD`, `SILVER` → XAGUSD
- `USOIL`, `OIL` → USOIL
- `BTCUSD`, `BITCOIN`, `BTC` → BTCUSD

### Validation
- SL doit être du bon côté (BUY → SL < entry, SELL → SL > entry)
- Prix range : 1000-9999
- Spam filter : `hit`, `pips` + 17 mots-clés + standalone filter

### Commentaire MT5
- Chaque ordre porte un commentaire `CHn-Cm` (ex: `CH2-C1`)
- `CHn` = numéro du canal Telegram (TG_CHANNEL_1 → CH1, etc.)
- `C1` = CAS 1 (prix dans la zone), `C2` = CAS 2 (prix hors zone)

## 📊 Stratégie d'exécution

### CAS 1 : Prix dans la zone d'entrée
- **MARKET** (50% lot) → TP = tp_final
- **LIMIT** (50% lot) entre SL et zone → TP = tp_final
- **Scénario A** : LIMIT non exécuté → annuler LIMIT, MARKET trailing seul
- **Scénario B** : LIMIT exécuté → fermer MARKET à TP3, SL du LIMIT = entrée MARKET, trailing

### CAS 2 : Prix hors zone
- **LIMIT_1** (50% lot) au bord de la zone → TP = tp_final
- **LIMIT_2** (50% lot) côté opposé de la zone → TP = tp_final
- **Scénario A** : aucun rempli, TP3 atteint → annuler les 2
- **Scénario B** : limit_1 rempli → annuler limit_2, SL limit_1 = entrée limit_1, trailing
- **Scénario C** : les 2 remplis → fermer limit_1 à TP3, SL limit_2 = entrée limit_1, trailing
- **Scénario D** : SL touché → tout fermé

### TP_TRIGGER (déclencheur BE/trailing)
- Configurable via workflow input `tp_trigger` (défaut : 3)
- Déclenche le BE + trailing à TPn au lieu de TP2
- Si `TP_TRIGGER > nombre de TPs du signal` → signal ignoré
- Si `tp3 == tp_final` → fallback automatique à `TP_TRIGGER - 1`
- Variable env : `TP_TRIGGER`

### Trailing SL
- Gap : 2$ (TRAIL_POINTS=200 pour XAUUSD)
- Activé uniquement après TP_TRIGGER atteint
- SL suit le prix à distance fixe (2$)

### Gestion du risque
- Max positions : 6
- Max spread : 50 points
- Filtre news Forex Factory (HIGH impact USD/XAU)
- Filtre horaire désactivé temporairement

## 🗄️ Supabase Schema
- **sessions** : runtime, channels, lot_size, mode, status
- **trades** : symbol, action, zone, SL, TPs, result, PnL, durée
- **events** : OPEN, CLOSE, TP_HIT, SL_HIT, SL_MOVE (JSONB details)
- **Vues** : `canal_stats`, `session_stats`

## 🚀 Déploiement (GitHub Actions)
- Windows Server sur GitHub Actions (windows-latest)
- Tailscale pour accès réseau privé
- RustDesk pour accès RDP
- Chrome Remote Desktop optionnel
- MT5 Exness à installer manuellement (setup sur Bureau)
- Bot dans `C:\TradingBot\`
- Durée max : ~355 min par run
- Cleanup .env automatique à la fin

## 📝 Historique des versions
- **v4.6.0** (2026-05-14) : suppression filtre SL 0.5%, fix parser superscript TP sans espace, spam filter `hit`/`pips`, commentaire MT5 CHn-Cm
- **v4.5.1** (2026-05-14) : filtre SL malformé (< 0.5% entry), bug CAS 2 retrigger, cleanup
- **v4.5.0** (2026-05-14) : gestion trades CAS 1/2 refonte, TP_TRIGGER dynamique, trailing 2$
- **v4.4.0** (2026-05-14) : 6 canaux TG, parser V5.1, suppression rapports TG
- **v4.3.2** (2026-05-05) : diagnostic logs, TradeReporter fix
- **v4.2** : async-safe, volume broker, BE/trailing fixes
- **v4.1** : parser V5, CAS 1/2, thread-safe locks

## ⚠️ Points d'attention
- Le bot utilise `telegram_listener_v4.py` (pas v4.4.py)
- Le parser est dans `signal_parser.py` (importé, pas inline)
- `bot.env` contient les canaux et paramètres fixes
- Les secrets (MT5, TG) sont injectés par le workflow GitHub Actions
- Le filtre horaire est désactivé (TIME_FILTER_ENABLED = False)
- REPORT_CHANNEL n'est plus utilisé (rapports TG supprimés)
- `TP_TRIGGER` est configurable via le workflow (input `tp_trigger`, défaut 3)
- Le trailing est en gap fixe de 2$ (TRAIL_POINTS=200), pas en points MT5
