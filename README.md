# 🤖 Trading Bot V4 — Telegram → MT5 Copy Trading

Bot de copy trading qui lit les signaux Telegram et les exécute automatiquement sur MetaTrader 5.

---

## 🚀 Fonctionnalités

- **📡 Parser de signaux V5** — Supporte 6 formats de canaux Telegram (BUY/SELL, SMC, DAILY, SL_MOVE, CLOSE...)
- **⚡ Exécution MT5** — Market orders, limit orders, BE, trailing stop
- **📊 Dashboard Streamlit** — P&L live, stats par canal, historique des trades
- **💾 Supabase** — Logging async avec retry exponentiel
- **🖥️ RDP + Tailscale** — Déploiement automatisé via GitHub Actions

---

## 📁 Structure

```
├── telegram_listener_v4.py    # Bot principal (Telegram → MT5)
├── signal_parser.py           # Parser de signaux V5 (6 formats)
├── supabase_logger.py         # Logger Supabase async
├── dashboard.py               # Dashboard Streamlit
├── dashboard-preview.html     # Aperçu statique du dashboard
├── requirements.txt           # Dépendances Python
├── .env.example               # Variables d'environnement
├── .streamlit/config.toml     # Config Streamlit (thème dark)
├── .github/workflows/         # CI/CD RDP + Tailscale
├── commande.txt               # Notes de développement
├── supabase_schema.sql        # Schéma BDD Supabase
└── HTF_Gold_EA_*.mq5          # Expert Advisor MetaTrader (diverses versions)
```

---

## ⚙️ Installation

### 1. Cloner le repo

```bash
git clone https://github.com/slh04ninja-cmyk/gzl2.git
cd gzl2
```

### 2. Installer les dépendances

```bash
pip install -r requirements.txt
```

### 3. Configurer les variables d'environnement

```bash
cp .env.example .env
# Remplir avec vos vraies valeurs
```

### 4. Lancer le bot

```bash
python telegram_listener_v4.py
```

### 5. Lancer le dashboard

```bash
streamlit run dashboard.py
```

---

## 📊 Dashboard

Le dashboard Streamlit affiche :
- **Statut** de la session active (running/stopped)
- **Métriques** : P&L, nombre de trades, win rate, trades ouverts
- **Performance par canal** avec graphique en barres
- **Courbe de P&L** cumulée
- **Historique** des 50 derniers trades
- **Configuration** de la session

Aperçu statique : ouvrir `dashboard-preview.html` dans un navigateur.

---

## 🔧 Configuration

| Variable | Description |
|----------|-------------|
| `TG_API_ID` | API ID Telegram (my.telegram.org) |
| `TG_API_HASH` | API Hash Telegram |
| `SUPABASE_URL` | URL du projet Supabase |
| `SUPABASE_ANON_KEY` | Clé anon Supabase |
| `MT5_LOGIN` | Login MetaTrader 5 |
| `MT5_PASSWORD` | Mot de passe MT5 |
| `MT5_SERVER` | Serveur du broker |

---

## 📄 License

Open-source — utilisez, modifiez et partagez librement.
