"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 4.0 — Corrigé, Restructuré + Performance Tracker
=============================================================
 Nouveautés v3.1 :
 - PerformanceTracker : log CSV + stats par canal
 - Rapport final + CSV envoyés sur Telegram avant fermeture
 - Timer de fermeture : rapport envoyé 5 min avant la fin
 - SIGTERM handler : arrêt propre si timeout GitHub
 - Shutdown automatique avec envoi de rapport
"""

import asyncio
import re
import logging
import time
import json
import urllib.request
import csv
import signal
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dotenv import load_dotenv
import threading

from telethon import TelegramClient, events
import MetaTrader5 as mt5

# Constantes de filling mode (pas toujours présentes dans toutes les versions du package MT5)
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
ORDER_FILLING_RETURN = 0
ORDER_FILLING_FOK = 1
ORDER_FILLING_IOC = 2

load_dotenv()

# ------------------------------------------------------------------
# SUPABASE LOGGER (initialized later)
# ------------------------------------------------------------------
_supa = None
_supa_connected = False

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
CHANNEL_NAME = os.getenv("TG_CHANNEL", "")
CHANNEL_NAME_2 = os.getenv("TG_CHANNEL_2", "")
CHANNEL_NAME_3 = os.getenv("TG_CHANNEL_3", "")
CHANNEL_NAME_4 = os.getenv("TG_CHANNEL_4", "")
REPORT_CHANNEL = os.getenv("TG_REPORT_CHANNEL", "")

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")

MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20250226"))
SLIPPAGE = int(os.getenv("SLIPPAGE", "20"))
ORDER_EXPIRY_MIN = int(os.getenv("ORDER_EXPIRY_MINUTES", "240"))
TRAIL_POINTS = float(os.getenv("TRAIL_POINTS", "150"))
LOT_SIZE = float(os.getenv("LOT_TOTAL", "0.01"))

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

NEWS_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "true").lower() == "true"
NEWS_BLOCK_MIN = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK", "15"))
NEWS_CLOSE_MIN = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE", "5"))
NEWS_AFTER_MIN = int(os.getenv("NEWS_WINDOW_AFTER", "15"))

TIME_FILTER_ENABLED = os.getenv("TIME_FILTER_ENABLED", "true").lower() == "true"

# Shutdown timer
RUNTIME_MINUTES = int(os.getenv("RUNTIME_MINUTES", "0"))
SHUTDOWN_MARGIN_MIN = 5  # envoyer le rapport 5 min avant la fin

# TP: Open config — Risk/Reward TP generation
OPEN_TP_RR_RATIOS = [float(x) for x in os.getenv("OPEN_TP_RR_RATIOS", "1.0,2.0,3.0").split(",")]
OPEN_TP_COUNT = int(os.getenv("OPEN_TP_COUNT", "3"))
OPEN_TRAIL_AFTER_TP = int(os.getenv("OPEN_TRAIL_AFTER_TP", "1"))  # trail after TP number N

START_TIME = datetime.now()
_shutdown_event = asyncio.Event()
_report_sent = False


def _parse_blocked_windows(raw: str) -> list:
    windows = []
    for w in raw.split(","):
        w = w.strip()
        if not w:
            continue
        try:
            start, end = w.split("-")
            h1, m1 = map(int, start.strip().split(":"))
            h2, m2 = map(int, end.strip().split(":"))
            windows.append((h1, m1, h2, m2))
        except Exception:
            pass
    return windows


_raw_windows = os.getenv("TIME_BLOCKED_WINDOWS", "13:00-15:00,16:30-17:30")
BLOCKED_WINDOWS = _parse_blocked_windows(_raw_windows)

# ------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------
class OrderFilter(logging.Filter):
    HIDE = ["[SPAM]", "[CYCLE]"]

    def filter(self, record):
        msg = record.getMessage()
        for tag in self.HIDE:
            if tag in msg:
                return False
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot_trading.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ------------------------------------------------------------------
# SUPABASE INITIALIZATION
# ------------------------------------------------------------------
try:
    from supabase_logger import SupabaseLogger
    _supa = SupabaseLogger()
    _supa_connected = _supa.connect()
except ImportError:
    _supa = None
    _supa_connected = False
    log.warning("supabase_logger non trouvé — mode CSV seul")

console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
console_handler.addFilter(OrderFilter())
log.addHandler(console_handler)

# ------------------------------------------------------------------
# FILTRE MESSAGES NON-TRADING
# ------------------------------------------------------------------
EXCLUDE_KEYWORDS = [
    "tp hit", "tp1 hit", "tp2 hit", "tp3 hit", "tp reached",
    "all tp hit", "mission acomplished", "boom boom boom",
    "my signal are on fire", "pips profit", "pips gain",
    "target", "closed at", "exit at", "sl hit", "stopped",
    "secured", "hit target", "be safe", "good luck",
    "market update", "analysis", "running",
    "are you in big loss", "contact",
    "use proper money management",
    "consistency",
]


def is_spam(text: str) -> bool:
    low = text.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in low:
            return True
    return False


# ------------------------------------------------------------------
# GESTION FENÊTRES HORAIRES BLOQUÉES
# ------------------------------------------------------------------
def in_blocked_window() -> tuple[bool, str]:
    if not TIME_FILTER_ENABLED:
        return False, ""
    now = datetime.now(timezone.utc)
    now_minutes = now.hour * 60 + now.minute
    for (h1, m1, h2, m2) in BLOCKED_WINDOWS:
        start = h1 * 60 + m1
        end = h2 * 60 + m2
        if start <= now_minutes < end:
            desc = f"{h1:02d}h{m1:02d}-{h2:02d}h{m2:02d} UTC"
            return True, desc
    return False, ""


# =============================================================
# PERFORMANCE TRACKER
# =============================================================
class PerformanceTracker:

    CSV_FILE = "trades_log.csv"
    CSV_HEADERS = [
        "date", "time", "canal", "symbol", "action",
        "zone_low", "zone_high", "sl", "tp_count",
        "tp_final", "result", "pnl", "duree_min"
    ]

    def __init__(self):
        self._ensure_csv()
        self._trades_cache = []
        self._report_sent = False

    def _ensure_csv(self):
        if not os.path.exists(self.CSV_FILE):
            with open(self.CSV_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADERS)
            log.info(f"[PERF] CSV créé : {self.CSV_FILE}")

    def log_trade_open(self, entry):
        sig = entry["signal"]
        now = datetime.now()
        row = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "canal": sig.get("source_channel", "Inconnu"),
            "symbol": sig["symbol"],
            "action": sig["action"],
            "zone_low": sig["zone_low"],
            "zone_high": sig["zone_high"],
            "sl": sig["sl"],
            "tp_count": len(sig["tps"]),
            "tp_final": sig["tps"][-1],
            "result": "OPEN",
            "pnl": 0.0,
            "duree_min": 0,
            "_entry_time": now,
            "_entry": entry,
        }
        self._trades_cache.append(row)

    def log_trade_close(self, entry, total_pnl):
        sig = entry["signal"]
        canal = sig.get("source_channel", "Inconnu")
        now = datetime.now()
        result = "WIN" if total_pnl > 0 else ("BE" if total_pnl == 0 else "LOSS")

        for t in reversed(self._trades_cache):
            if (t["canal"] == canal and
                t["symbol"] == sig["symbol"] and
                t["action"] == sig["action"] and
                t["result"] == "OPEN"):
                entry_time = t.get("_entry_time", now)
                duree = (now - entry_time).total_seconds() / 60
                t["result"] = result
                t["pnl"] = round(total_pnl, 2)
                t["duree_min"] = round(duree, 1)
                self._append_csv(t)
                break
        else:
            row = {
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "canal": canal,
                "symbol": sig["symbol"],
                "action": sig["action"],
                "zone_low": sig["zone_low"],
                "zone_high": sig["zone_high"],
                "sl": sig["sl"],
                "tp_count": len(sig["tps"]),
                "tp_final": sig["tps"][-1],
                "result": result,
                "pnl": round(total_pnl, 2),
                "duree_min": 0,
            }
            self._append_csv(row)

    def _append_csv(self, row):
        with open(self.CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                row["date"], row["time"], row["canal"],
                row["symbol"], row["action"],
                row["zone_low"], row["zone_high"], row["sl"],
                row["tp_count"], row["tp_final"],
                row["result"], row["pnl"], row["duree_min"],
            ])

    def get_stats_by_channel(self) -> dict:
        if not os.path.exists(self.CSV_FILE):
            return {}

        trades = []
        with open(self.CSV_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["result"] in ("OPEN", ""):
                    continue
                trades.append(row)

        stats = defaultdict(lambda: {
            "trades": 0, "wins": 0, "losses": 0, "be": 0,
            "pnl_total": 0.0, "pnl_wins": 0.0, "pnl_losses": 0.0,
            "best": 0.0, "worst": 0.0,
        })

        for t in trades:
            canal = t["canal"]
            pnl = float(t["pnl"])
            s = stats[canal]
            s["trades"] += 1
            s["pnl_total"] += pnl
            if pnl > 0:
                s["wins"] += 1
                s["pnl_wins"] += pnl
                s["best"] = max(s["best"], pnl)
            elif pnl < 0:
                s["losses"] += 1
                s["pnl_losses"] += abs(pnl)
                s["worst"] = min(s["worst"], pnl)
            else:
                s["be"] += 1

        for canal, s in stats.items():
            if s["trades"] > 0:
                s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1)
            else:
                s["win_rate"] = 0
            if s["pnl_losses"] > 0:
                s["profit_factor"] = round(s["pnl_wins"] / s["pnl_losses"], 2)
            else:
                s["profit_factor"] = (
                    float("inf") if s["pnl_wins"] > 0 else 0
                )
            s["pnl_total"] = round(s["pnl_total"], 2)
            s["avg_win"] = (
                round(s["pnl_wins"] / s["wins"], 2) if s["wins"] > 0 else 0
            )
            s["avg_loss"] = (
                round(s["pnl_losses"] / s["losses"], 2)
                if s["losses"] > 0 else 0
            )
            if s["avg_loss"] > 0:
                s["rr_ratio"] = round(s["avg_win"] / s["avg_loss"], 2)
            else:
                s["rr_ratio"] = 0

        return dict(stats)

    def format_report(self) -> str:
        stats = self.get_stats_by_channel()
        if not stats:
            return "📊 Aucun trade enregistré pour le moment."

        sorted_channels = sorted(
            stats.items(),
            key=lambda x: x[1]["pnl_total"],
            reverse=True,
        )

        total_trades = sum(s["trades"] for _, s in sorted_channels)
        total_pnl = sum(s["pnl_total"] for _, s in sorted_channels)

        lines = [
            "📊 RAPPORT DE PERFORMANCE",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {datetime.now():%Y-%m-%d %H:%M}",
            f"📈 Total : {total_trades} trades | {total_pnl:+.2f}$",
            "",
        ]

        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        for i, (canal, s) in enumerate(sorted_channels):
            medal = medals[i] if i < len(medals) else f"{i + 1}."
            emoji = "✅" if s["pnl_total"] > 0 else "❌"
            lines.append(f"{medal} {canal} {emoji}")
            lines.append(
                f"   Trades: {s['trades']} | "
                f"Win: {s['win_rate']}% | "
                f"P&L: {s['pnl_total']:+.2f}$"
            )
            lines.append(
                f"   PF: {s['profit_factor']} | "
                f"R:R: {s['rr_ratio']} | "
                f"Best: {s['best']:+.2f}$"
            )
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        best = sorted_channels[0]
        worst = sorted_channels[-1]
        if best[1]["pnl_total"] > 0:
            lines.append(
                f"🏆 Meilleur canal : {best[0]} ({best[1]['pnl_total']:+.2f}$)"
            )
        if worst[1]["pnl_total"] < 0:
            lines.append(
                f"⚠️ Pire canal : {worst[0]} ({worst[1]['pnl_total']:+.2f}$)"
            )

        return "\n".join(lines)

    def format_session_summary(self) -> str:
        if not self._trades_cache:
            return "📊 Aucun trade cette session."

        wins = sum(1 for t in self._trades_cache if t["result"] == "WIN")
        losses = sum(1 for t in self._trades_cache if t["result"] == "LOSS")
        be = sum(1 for t in self._trades_cache if t["result"] == "BE")
        still_open = sum(
            1 for t in self._trades_cache if t["result"] == "OPEN"
        )
        total_pnl = sum(t["pnl"] for t in self._trades_cache)

        lines = [
            "📊 RÉSUMÉ SESSION",
            "━━━━━━━━━━━━━━━━━━",
            f"✅ Wins : {wins}",
            f"❌ Losses : {losses}",
            f"⬜ Breakeven : {be}",
            f"🔵 Ouverts : {still_open}",
            f"💰 P&L session : {total_pnl:+.2f}$",
        ]
        return "\n".join(lines)

    async def send_csv_to_telegram(self, reporter):
        if not os.path.exists(self.CSV_FILE):
            return
        try:
            await reporter.send_tg(
                "📄 Voici le fichier de trades de cette session :"
            )
            if reporter._tg_client and reporter._report_entity:
                await reporter._tg_client.send_file(
                    reporter._report_entity,
                    self.CSV_FILE,
                    caption=(
                        f"📊 trades_log.csv — {datetime.now():%Y-%m-%d %H:%M}"
                    ),
                )
                log.info("[PERF] CSV envoyé sur Telegram")
        except Exception as e:
            log.error(f"[PERF] Erreur envoi CSV : {e}")

    async def send_final_report(self, reporter):
        if self._report_sent:
            return
        self._report_sent = True
        log.info("[PERF] Envoi du rapport final...")

        # 1. Résumé session
        summary = self.format_session_summary()
        await reporter.send_tg(summary)

        # 2. Rapport complet par canal
        report = self.format_report()
        await reporter.send_tg(report)

        # 3. Envoyer le CSV
        await self.send_csv_to_telegram(reporter)


# =============================================================
# NEWS MANAGER
# =============================================================
class NewsManager:

    FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    def __init__(self, bridge):
        self.bridge = bridge
        self.manager = None
        self._news = []
        self._blocked = False
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_manager(self, manager):
        self.manager = manager

    def is_blocked(self) -> bool:
        return self._blocked

    def _loop(self):
        while not self._stop:
            try:
                self._fetch_news()
                self._check_news()
            except Exception as e:
                log.error(f"NewsManager erreur: {e}")
            for _ in range(30):
                if self._stop:
                    break
                time.sleep(60)

    def _fetch_news(self):
        try:
            req = urllib.request.Request(
                self.FF_URL, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            self._news = [
                n for n in data
                if n.get("impact", "").lower() == "high"
                and n.get("currency", "") in ("USD", "XAU")
            ]
            log.info(f"[NEWS] {len(self._news)} news HIGH impact chargées")
        except Exception as e:
            log.error(f"[NEWS] Erreur fetch: {e}")

    def _check_news(self):
        if not NEWS_ENABLED:
            return
        now = datetime.now(timezone.utc)
        for news in self._news:
            try:
                news_time = datetime.fromisoformat(
                    news["date"].replace("Z", "+00:00")
                )
            except Exception:
                continue
            diff_minutes = (news_time - now).total_seconds() / 60

            if -NEWS_AFTER_MIN <= diff_minutes < 0 and self._blocked:
                remaining = NEWS_AFTER_MIN + diff_minutes
                if remaining <= 0:
                    self._blocked = False
                    log.info(
                        f"[NEWS] {news.get('title', '?')} terminé → reprise"
                    )
                    break

            if 0 < diff_minutes <= NEWS_CLOSE_MIN:
                if not self._blocked:
                    self._blocked = True
                    log.info(
                        f"[NEWS] {news.get('title', '?')} dans "
                        f"{diff_minutes:.0f} min → fermeture positions"
                    )
                    if self.manager:
                        self._close_all()
                    break

            elif NEWS_CLOSE_MIN < diff_minutes <= NEWS_BLOCK_MIN:
                if not self._blocked:
                    self._blocked = True
                    log.info(
                        f"[NEWS] {news.get('title', '?')} dans "
                        f"{diff_minutes:.0f} min → signaux bloqués"
                    )
                    break

    def _close_all(self):
        if self.manager:
            with self.manager._lock:
                for entry in list(self.manager.active):
                    for o in entry.get("orders", []):
                        self.bridge.cancel_order(o["order"])
                    entry["orders"] = []
            self.bridge.close_all()

    def stop(self):
        self._stop = True


# =============================================================
# TRADE REPORTER
# =============================================================
class TradeReporter:

    def __init__(self):
        self._tg_client = None
        self._report_entity = None
        self._loop = None

    async def set_telegram_client(self, client: TelegramClient):
        self._tg_client = client
        self._loop = asyncio.get_running_loop()
        if REPORT_CHANNEL:
            try:
                self._report_entity = await client.get_entity(REPORT_CHANNEL)
                log.info(
                    f"Canal de rapport : "
                    f"{getattr(self._report_entity, 'title', REPORT_CHANNEL)}"
                )
            except Exception as e:
                log.warning(f"Canal de rapport introuvable : {e}")
                self._report_entity = None

    async def send_tg(self, message: str):
        if self._tg_client and self._report_entity:
            try:
                await self._tg_client.send_message(
                    self._report_entity, message
                )
            except Exception as e:
                log.error(f"Erreur envoi rapport TG : {e}")

    async def on_order_opened(self, entry):
        sig = entry["signal"]
        canal = sig.get("source_channel", "Inconnu")
        zone = f"{sig['zone_low']}-{sig['zone_high']}"
        all_tps = sig["tps"]
        tps_str = ", ".join([f"TP{i + 1}={v}" for i, v in enumerate(all_tps)])

        mode_tag = "🧪 DEMO" if DEMO_MODE else "💰 LIVE"
        msg = (
            f"🟢 ORDRE OUVERT {mode_tag}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"📡 Canal : {canal}\n"
            f"📊 {sig['symbol']} {sig['action']}\n"
            f"📍 Zone : {zone}\n"
            f"❌ SL : {sig['sl']}\n"
            f"🎯 {tps_str}\n"
            f"📦 Lot : {LOT_SIZE} × {len(entry['tickets'])} position(s)\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await self.send_tg(msg)

    async def on_tp_reached(self, ticket, sig, tp_name, tp_value, pnl):
        canal = sig.get("source_channel", "Inconnu")
        msg = (
            f"🎯 {tp_name} ATTEINT\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"📡 Canal : {canal}\n"
            f"📊 {sig['symbol']} {sig['action']}\n"
            f"🎯 {tp_name} : {tp_value}\n"
            f"💰 P&L : {pnl:+.2f} $\n"
            f"🎫 Ticket : #{ticket}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await self.send_tg(msg)

    async def on_sl_hit(self, ticket, sig, pnl):
        canal = sig.get("source_channel", "Inconnu")
        zone = f"{sig['zone_low']}-{sig['zone_high']}"
        msg = (
            f"🔴 SL TOUCHÉ\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"📡 Canal : {canal}\n"
            f"📊 {sig['symbol']} {sig['action']}\n"
            f"📍 Zone : {zone}\n"
            f"❌ SL : {sig['sl']}\n"
            f"💸 P&L : {pnl:+.2f} $\n"
            f"🎫 Ticket : #{ticket}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await self.send_tg(msg)

    async def on_trade_closed(self, entry, total_pnl):
        sig = entry["signal"]
        canal = sig.get("source_channel", "Inconnu")
        zone = f"{sig['zone_low']}-{sig['zone_high']}"
        emoji = "✅" if total_pnl >= 0 else "❌"
        mode_tag = "🧪 DEMO" if DEMO_MODE else "💰 LIVE"
        msg = (
            f"{emoji} TRADE FERMÉ {mode_tag}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"📡 Canal : {canal}\n"
            f"📊 {sig['symbol']} {sig['action']}\n"
            f"📍 Zone : {zone}\n"
            f"❌ SL : {sig['sl']}\n"
            f"💰 P&L TOTAL : {total_pnl:+.2f} $\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await self.send_tg(msg)


# =============================================================
# SIGNAL PARSER
# =============================================================
class SignalParser:

    SYMBOL_MAP = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "OIL": "USOIL", "BTC": "BTCUSD", "BITCOIN": "BTCUSD"}

    RE_MAIN = re.compile(
        r"\(?(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL|BTCUSD|BTC/USD|BTC)\)?\s+"
        r"(?:INSTANT\s+|NOW[:\s]*)?(?:INSTANT\s+)?(BUY|SELL)"
        r"[^\d\n]{0,30}?(?:Entry:\s*)?\(?\s*([\d.]+)\s*[-/_\s]\s*([\d.]+)?\s*\)?",
        re.IGNORECASE,
    )

    RE_MAIN_REV = re.compile(
        r"(BUY|SELL)\s+"
        r"(?:.*?(?:Pair|Symbol)\s*[:.]?\s*)?"
        r"\(?(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL|BTCUSD|BTC/USD|BTC)\)?"
        r"[^\d]{0,80}?(?:Price|Entry)\s*[:.]\s*\(?\s*([\d.]+)\s*[-/_\s]\s*([\d.]+)?\s*\)?",
        re.IGNORECASE | re.DOTALL,
    )

    # Type: SELL / Pair: XAUUSD / Entry Price: ... format
    RE_TYPE_FORMAT = re.compile(
        r"Type\s*[:.]\s*(BUY|SELL)",
        re.IGNORECASE,
    )

    RE_PAIR_FORMAT = re.compile(
        r"Pair\s*[:.]\s*\(?([\w/]+)\)?",
        re.IGNORECASE,
    )

    RE_ENTRY_PRICE = re.compile(
        r"(?:Entry\s*)?Price\s*[:.]\s*([\d.]+)\s*[-/_\s]\s*([\d.]+)",
        re.IGNORECASE,
    )

    RE_MAIN_ALT = re.compile(
        r"(BUY|SELL)\s+TRADE\s+\(?(XAUUSD|GOLD|XAU/USD|BTCUSD|BTC/USD|BTC)\)?", re.IGNORECASE
    )

    RE_ZONE_LINE = re.compile(
        r"^\s*([\d.]+)\s*[-/]\s*([\d.]+)\s*$", re.MULTILINE
    )

    RE_TARGET_OPEN = re.compile(
        r"(?:Target|TP)\s*:\s*open", re.IGNORECASE
    )

    RE_SL_TARGET = re.compile(
        r"(?:📍\s*)?Stop\s+Loss\s*:\s*([\d.]+)", re.IGNORECASE
    )

    RE_TP = re.compile(
        r"(?:\U0001F3AF|\U0001F4CA|\u27A4|\u25BA|\u25B6)?\s*"
        r"\bTP(?:\s*\(\s*TP\d+\s*\))?"
        r"[-.: ]\s*"
        r"([\d]{3,}(?:\.\d+)?)(?:-max[\d.]+)?",
        re.IGNORECASE,
    )

    RE_TP_TAKE = re.compile(
        r"(?:Take\s+Profit|TAKE\s+PROFIT)\s*\d*\s*(?:\(\s*TP\d+\s*\))?\s*[:.]?\s*([\d.]+)",
        re.IGNORECASE,
    )

    RE_TP_LONG = re.compile(
        r"TAKE\s+PROFIT\s*[:.]\s*(?:\d+\s+)?\(?\s*"
        r"([\d]{4,}(?:\.\d+)?)\s*\)?(?:\s*CONFIRM\S*)?",
        re.IGNORECASE,
    )

    RE_SL = re.compile(
        r"(?:\U0001F534|\u274C|\U0001F6D1|\u26D4|\U0001F44E)?\s*"
        r"SL[-.\s]{0,5}([\d]{3,}(?:\.\d+)?)",
        re.IGNORECASE,
    )

    RE_SL_LONG = re.compile(
        r"STOP(?:\s+)?LOSS\s*(?:\(\s*SL\s*\))?\s*[:.]\s*\(?\s*([\d]{3,}(?:\.\d+)?)\s*\)?",
        re.IGNORECASE,
    )

    RE_SL_MOVE = re.compile(
        r"(?:SL\s*MOVE|MOVE\s*SL|New\s*SL|SL\s*\u2192|SL\s*moved?\s*to)"
        r"\s*[:\s]*\s*([\d.]+)",
        re.IGNORECASE,
    )

    RE_SL_ALONE = re.compile(
        r"^\s*(?:\U0001F534|\u274C|\U0001F6D1)?\s*SL\s*[.:\s]+\s*"
        r"([\d]{3,}(?:\.\d+)?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    # --- Purchase / Sell Order format (✅...❌) ---
    RE_PURCHASE_ORDER = re.compile(
        r"(?:✅\s*)?"
        r"([\w/]+)\s*(?:\([\w/]+\))?\s*"
        r"(Purchase|Sell)\s+Order",
        re.IGNORECASE,
    )

    RE_PURCHASE_ENTRY = re.compile(
        r"Entry\s+Price\s*([\d.]+)\s*[-/]\s*([\d.]+)",
        re.IGNORECASE,
    )

    RE_PURCHASE_TP = re.compile(
        r"TP[¹²³⁴⁵⁶⁷⁸⁹\d]*\s+([\d.]+)",
        re.IGNORECASE,
    )

    RE_PURCHASE_SL = re.compile(
        r"(?:❌\s*)?Stop\s+Loss\s*:\s*([\d.]+)(?:\s*❌)?",
        re.IGNORECASE,
    )

    RE_CLOSE = re.compile(r"close\s+(all|[A-Z]{3,10})", re.IGNORECASE)

    RE_DAILY_ACTION = re.compile(
        r"(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL|BTCUSD|BTC/USD|BTC)\s+DAILY\s+SIGNAL",
        re.IGNORECASE,
    )
    RE_DAILY_DIR = re.compile(r"Action\s*:\s*(BUY|SELL)", re.IGNORECASE)
    RE_DAILY_ENTRY = re.compile(
        r"Entry\s+Price\s*\n\s*([\d.]+)\s*[-/\s]\s*([\d.]+)", re.IGNORECASE
    )
    RE_DAILY_TP = re.compile(r"TP\d+\s*:\s*([\d.]+)", re.IGNORECASE)
    RE_DAILY_SL = re.compile(
        r"Stop\s+Loss\s*(?:\(SL\))?\s*:\s*([\d.]+)", re.IGNORECASE
    )

    RE_TAKE_PROFITE = re.compile(
        r"TAKE\s+PROFITE?\s*:\s*\d+\s*\(\s*([\d.]+)\s*\)", re.IGNORECASE
    )

    RE_TP_LINE = re.compile(
        r"^\s*TP\s+([\d.]+)\s*$", re.IGNORECASE | re.MULTILINE
    )

    @staticmethod
    def _detect_action(zone_low, zone_high, tps):
        """Detect BUY/SELL by comparing entry zone vs TPs average."""
        avg_entry = (zone_low + zone_high) / 2
        avg_tp = sum(tps) / len(tps)
        return "SELL" if avg_tp < avg_entry else "BUY"

    def _build_result(
        self, symbol, action, zone_low, zone_mid, zone_high, tps, sl
    ):
        # Always auto-detect direction from prices
        action = self._detect_action(zone_low, zone_high, tps)
        return {
            "type": "TRADE",
            "symbol": symbol,
            "action": action,
            "zone_low": zone_low,
            "zone_mid": zone_mid,
            "zone_high": zone_high,
            "tps": tps,
            "tp1": tps[0],
            "tp2": tps[1] if len(tps) >= 2 else tps[-1],
            "tp3": tps[2] if len(tps) >= 3 else tps[-1],
            "tp4": tps[3] if len(tps) >= 4 else tps[-1],
            "tp_final": tps[-1],
            "sl": sl,
        }

    def parse(self, text: str) -> dict | None:

        # Try Type: SELL / Pair: XAUUSD format
        type_result = self._parse_type_format(text)
        if type_result:
            return type_result

        # Try Purchase/Sell Order format (✅...❌)
        po_result = self._parse_purchase_order(text)
        if po_result:
            return po_result

        close_m = self.RE_CLOSE.search(text.upper())
        if close_m:
            target = close_m.group(1).upper()
            return {
                "type": "CLOSE",
                "symbol": None if target == "ALL" else target,
                "close_all": target == "ALL",
            }

        sl_move_m = self.RE_SL_MOVE.search(text)
        if sl_move_m:
            return {
                "type": "SL_MOVE",
                "new_sl": float(sl_move_m.group(1)),
            }

        daily_m = self.RE_DAILY_ACTION.search(text)
        if daily_m:
            action_m = self.RE_DAILY_DIR.search(text)
            entry_m = self.RE_DAILY_ENTRY.search(text)
            sl_m = self.RE_DAILY_SL.search(text)
            tp_vals = [float(v) for v in self.RE_DAILY_TP.findall(text)]
            if action_m and entry_m and tp_vals and sl_m:
                sym = self.SYMBOL_MAP.get(
                    daily_m.group(1).upper(), daily_m.group(1).upper()
                )
                act = action_m.group(1).upper()
                try:
                    pa = float(entry_m.group(1))
                    pb = float(entry_m.group(2))
                except ValueError:
                    return None
                zl, zh = min(pa, pb), max(pa, pb)
                zm = round((zl + zh) / 2, 2)
                sl = float(sl_m.group(1))
                log.info(
                    f"DAILY → {act} {sym} zone [{zl}—{zm}—{zh}] "
                    f"({len(tp_vals)} TPs)"
                )
                return self._build_result(sym, act, zl, zm, zh, tp_vals, sl)
            return None

        tp_profite = self.RE_TAKE_PROFITE.findall(text)
        if tp_profite:
            main_m = self.RE_MAIN.search(text)
            if main_m:
                sym = self.SYMBOL_MAP.get(
                    main_m.group(1).upper(), main_m.group(1).upper()
                )
                act = main_m.group(2).upper()
                try:
                    pa = float(main_m.group(3))
                    pb = float(main_m.group(4)) if main_m.group(4) else pa
                except ValueError:
                    return None
                zl, zh = min(pa, pb), max(pa, pb)
                zm = round((zl + zh) / 2, 2)
                tps = [float(v) for v in tp_profite]
                sl_m2 = self.RE_SL_LONG.search(text) or self.RE_SL.search(
                    text
                )
                sl = float(sl_m2.group(1)) if sl_m2 else None
                if not tps or sl is None:
                    return None
                log.info(f"TAKE PROFITE → {act} {sym} ({len(tps)} TPs)")
                return self._build_result(sym, act, zl, zm, zh, tps, sl)

        if is_spam(text):
            log.debug(f"[SPAM] {text[:60].replace(chr(10), ' ')}")
            return None

        main_m = self.RE_MAIN.search(text)
        if not main_m:
            main_m = self.RE_MAIN_REV.search(text)
            if main_m:
                # RE_MAIN_REV: group(1)=action, group(2)=symbol, group(3)=price1, group(4)=price2
                # Remap to match _parse_main expectations: group(1)=symbol, group(2)=action, group(3)=price1, group(4)=price2
                class _M:
                    def __init__(self, m):
                        self._m = m
                    def group(self, n):
                        remap = {1: 2, 2: 1, 3: 3, 4: 4}
                        return self._m.group(remap[n])
                main_m = _M(main_m)
        if not main_m:
            return self._parse_alt(text)
        return self._parse_main(main_m, text)

    def _parse_type_format(self, text: str) -> dict | None:
        """Parse Type: SELL / Pair: XAUUSD / Entry Price: ... format."""
        type_m = self.RE_TYPE_FORMAT.search(text)
        pair_m = self.RE_PAIR_FORMAT.search(text)
        entry_m = self.RE_ENTRY_PRICE.search(text)
        if not (type_m and pair_m and entry_m):
            return None

        action = type_m.group(1).upper()
        raw_sym = pair_m.group(1).upper().replace("/", "")
        sym = self.SYMBOL_MAP.get(raw_sym, raw_sym)

        try:
            pa = float(entry_m.group(1))
            pb = float(entry_m.group(2))
        except ValueError:
            return None
        zl, zh = min(pa, pb), max(pa, pb)
        zm = round((zl + zh) / 2, 2)

        # TPs: try specific patterns first
        tps = []
        for val in self.RE_TP_TAKE.findall(text):
            try:
                tps.append(float(val))
            except ValueError:
                pass
        if not tps:
            for val in self.RE_TP.findall(text):
                try:
                    tps.append(float(val))
                except ValueError:
                    pass
        if not tps:
            for val in self.RE_TP_LONG.findall(text):
                try:
                    tps.append(float(val))
                except ValueError:
                    pass

        sl_m = self.RE_SL.search(text)
        if not sl_m:
            sl_m = self.RE_SL_LONG.search(text)
        sl = float(sl_m.group(1)) if sl_m else None

        if not tps:
            tps = self._extract_tps(text, action, zl, zh, sl=sl)

        if not tps or sl is None:
            log.warning(f"TypeFormat incomplet TPs={tps} SL={sl} | {text[:80]}")
            return None

        log.info(
            f"TypeFormat → {action} {sym} zone [{zl}—{zm}—{zh}] "
            f"TPfinal={tps[-1]} SL={sl} ({len(tps)} TPs)"
        )
        return self._build_result(sym, action, zl, zm, zh, tps, sl)

    def _parse_purchase_order(self, text: str) -> dict | None:
        """Parse ✅...Purchase/Sell Order...❌ format."""
        po_m = self.RE_PURCHASE_ORDER.search(text)
        if not po_m:
            return None

        raw_symbol = po_m.group(1).upper().strip()
        action_word = po_m.group(2).upper()

        sym = self.SYMBOL_MAP.get(raw_symbol, raw_symbol)
        # Also try mapping from parenthetical like (XAU/USD)
        paren_m = re.search(r"\(([\w/]+)\)", text)
        if paren_m:
            paren_sym = paren_m.group(1).upper().replace("/", "")
            sym = self.SYMBOL_MAP.get(paren_sym, paren_sym)

        entry_m = self.RE_PURCHASE_ENTRY.search(text)
        if not entry_m:
            return None

        try:
            pa = float(entry_m.group(1))
            pb = float(entry_m.group(2))
        except ValueError:
            return None
        zl, zh = min(pa, pb), max(pa, pb)
        zm = round((zl + zh) / 2, 2)

        tps = [float(v) for v in self.RE_PURCHASE_TP.findall(text)]
        sl_m = self.RE_PURCHASE_SL.search(text)
        sl = float(sl_m.group(1)) if sl_m else None

        if not tps or sl is None:
            log.warning(
                f"PurchaseOrder incomplet TPs={tps} SL={sl} | {text[:80]}"
            )
            return None

        log.info(
            f"PurchaseOrder → {sym} zone [{zl}—{zm}—{zh}] "
            f"TPfinal={tps[-1]} SL={sl} ({len(tps)} TPs)"
        )
        return self._build_result(sym, "", zl, zm, zh, tps, sl)

    def _parse_alt(self, text):
        alt_m = self.RE_MAIN_ALT.search(text)
        if alt_m:
            zone_m = self.RE_ZONE_LINE.search(text)
            if zone_m:
                act = alt_m.group(1).upper()
                sym = self.SYMBOL_MAP.get(
                    alt_m.group(2).upper().replace("/", ""),
                    alt_m.group(2).upper().replace("/", ""),
                )
                try:
                    pa = float(zone_m.group(1))
                    pb = float(zone_m.group(2))
                except ValueError:
                    return None
                zl, zh = min(pa, pb), max(pa, pb)
                zm = round((zl + zh) / 2, 2)
                sl_m = (
                    self.RE_SL_TARGET.search(text)
                    or self.RE_SL_LONG.search(text)
                    or self.RE_SL.search(text)
                )
                sl = float(sl_m.group(1)) if sl_m else None
                if sl is None:
                    return None
                tps = self._extract_tps(text, act, zl, zh)
                if not tps:
                    return None
                return self._build_result(sym, act, zl, zm, zh, tps, sl)

        sl_alone = self.RE_SL_ALONE.search(text)
        if sl_alone:
            return {
                "type": "SL_MOVE",
                "new_sl": float(sl_alone.group(1)),
            }

        tp_line_vals = self.RE_TP_LINE.findall(text)
        if tp_line_vals:
            first = text.strip().split("\n")[0]
            sym_m = re.search(
                r"(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL|BTCUSD|BTC/USD|BTC)",
                first,
                re.IGNORECASE,
            )
            dir_m = re.search(r"\b(BUY|SELL)\b", first, re.IGNORECASE)
            zone_m = self.RE_ZONE_LINE.search(first)
            if sym_m and dir_m and zone_m:
                sym = self.SYMBOL_MAP.get(
                    sym_m.group(1).upper(), sym_m.group(1).upper()
                )
                act = dir_m.group(1).upper()
                try:
                    pa = float(zone_m.group(1))
                    pb = float(zone_m.group(2))
                except ValueError:
                    return None
                zl, zh = min(pa, pb), max(pa, pb)
                zm = round((zl + zh) / 2, 2)
                sl_l = re.search(
                    r"^\s*SL\s+([\d.]+)",
                    text,
                    re.IGNORECASE | re.MULTILINE,
                )
                sl = float(sl_l.group(1)) if sl_l else None
                tps = [float(v) for v in tp_line_vals]
                if not tps or sl is None:
                    return None
                return self._build_result(sym, act, zl, zm, zh, tps, sl)
        return None

    def _extract_tps(self, text, action, zone_low, zone_high, sl=None):
        if self.RE_TARGET_OPEN.search(text):
            # R:R-based generation if SL available
            if sl is not None:
                avg_entry = (zone_low + zone_high) / 2
                risk = abs(avg_entry - sl)
                if risk > 0:
                    tps = []
                    for rr in OPEN_TP_RR_RATIOS[:OPEN_TP_COUNT]:
                        if action == "BUY":
                            tps.append(round(avg_entry + risk * rr, 2))
                        else:
                            tps.append(round(avg_entry - risk * rr, 2))
                    log.info(
                        f"TP:Open R:R → {action} risk={risk:.1f} "
                        f"TPs={tps}"
                    )
                    return tps
            # Fallback: fixed step
            step = 4.0
            base = zone_high if action == "BUY" else zone_low
            if action == "SELL":
                return [
                    round(base - step, 2),
                    round(base - step * 2, 2),
                    round(base - step * 3, 2),
                ]
            else:
                return [
                    round(base + step, 2),
                    round(base + step * 2, 2),
                    round(base + step * 3, 2),
                ]
        tps = []
        for val in self.RE_TP.findall(text):
            try:
                tps.append(float(val))
            except ValueError:
                pass
        if not tps:
            for val in self.RE_TP_TAKE.findall(text):
                try:
                    tps.append(float(val))
                except ValueError:
                    pass
        if not tps:
            for val in self.RE_TP_LONG.findall(text):
                try:
                    tps.append(float(val))
                except ValueError:
                    pass
        return tps

    def _parse_main(self, main_m, text):
        sym = self.SYMBOL_MAP.get(
            main_m.group(1).upper(), main_m.group(1).upper()
        )
        act = main_m.group(2).upper()
        try:
            pa = float(main_m.group(3))
            pb = float(main_m.group(4))
        except ValueError:
            log.warning(f"Valeurs invalides dans le signal: {text[:80]}")
            return None

        zl, zh = min(pa, pb), max(pa, pb)
        zm = round((zl + zh) / 2, 2)

        # SL first (needed for R:R TP generation)
        sl_m = self.RE_SL.search(text)
        if not sl_m:
            sl_m = self.RE_SL_LONG.search(text)
        sl = float(sl_m.group(1)) if sl_m else None

        tps = []
        for val in self.RE_TP.findall(text):
            try:
                tps.append(float(val))
            except ValueError:
                pass
        if not tps:
            for val in self.RE_TP_TAKE.findall(text):
                try:
                    tps.append(float(val))
                except ValueError:
                    pass
        if not tps:
            for val in self.RE_TP_LONG.findall(text):
                try:
                    tps.append(float(val))
                except ValueError:
                    pass
        if not tps:
            tps = self._extract_tps(text, act, zl, zh, sl=sl)

        if not tps or sl is None:
            log.warning(f"Incomplet TPs={tps} SL={sl} | {text[:80]}")
            return None

        log.info(
            f"Parsé → {act} {sym} zone [{zl}—{zm}—{zh}] "
            f"TPfinal={tps[-1]} SL={sl} ({len(tps)} TPs)"
        )
        return self._build_result(sym, act, zl, zm, zh, tps, sl)


# =============================================================
# MT5 BRIDGE
# =============================================================
class MT5Bridge:

    _sym_cache: dict = {}

    def connect(self) -> bool:
        if mt5.initialize():
            info = mt5.account_info()
            if info and info.login > 0:
                log.info(
                    f"MT5 déjà connecté → {info.name} | "
                    f"Balance: {info.balance} {info.currency}"
                )
                return self._check_algo()
        mt5.shutdown()

        if not mt5.initialize(
            login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER
        ):
            log.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        log.info(
            f"MT5 connecté → {info.name} | "
            f"Balance: {info.balance} {info.currency}"
        )
        return self._check_algo()

    def _check_algo(self) -> bool:
        terminal = mt5.terminal_info()
        try:
            algo_ok = bool(getattr(terminal, "trade_expert", True))
        except Exception:
            algo_ok = True
        if not algo_ok:
            log.warning("Vérifiez que 'Algo Trading' est VERT dans MT5")
        else:
            log.info("Algo Trading actif")
        return True

    def disconnect(self):
        mt5.shutdown()

    def _sym(self, symbol: str):
        if symbol in self._sym_cache:
            return mt5.symbol_info(self._sym_cache[symbol])
        info = mt5.symbol_info(symbol)
        if info is None:
            for sfx in [
                "m", "m+", ".a", "pro", "+", ".", "z", "micro", "#", ""
            ]:
                info = mt5.symbol_info(symbol + sfx)
                if info:
                    log.info(f"Symbole résolu : {symbol} → {symbol + sfx}")
                    break
        if info is None and symbol.endswith("m"):
            info = mt5.symbol_info(symbol[:-1])
            if info:
                log.info(f"Symbole résolu : {symbol} → {symbol[:-1]}")
        if info is None:
            all_syms = mt5.symbols_get()
            if all_syms:
                matches = [
                    s
                    for s in all_syms
                    if s.name.upper().startswith(symbol.upper()[:6])
                ]
                if matches:
                    info = matches[0]
                    log.info(f"Symbole trouvé par recherche : {info.name}")
        if info is None:
            log.error(f"Symbole introuvable : {symbol}")
            return None
        self._sym_cache[symbol] = info.name
        if not info.visible:
            mt5.symbol_select(info.name, True)
            time.sleep(0.5)
        return mt5.symbol_info(info.name)

    def _get_filling(self, sym_info) -> int:
        filling = sym_info.filling_mode
        if filling & SYMBOL_FILLING_FOK:
            return ORDER_FILLING_FOK
        if filling & SYMBOL_FILLING_IOC:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_RETURN

    def _force_filling(self, sym_info) -> int:
        """Essaie tous les modes de remplissage jusqu'à en trouver un qui marche."""
        candidates = [ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN]
        filling = sym_info.filling_mode
        if filling & SYMBOL_FILLING_FOK:
            return ORDER_FILLING_FOK
        if filling & SYMBOL_FILLING_IOC:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_RETURN

    def current_price(self, symbol: str, action: str) -> float | None:
        sym_info = self._sym(symbol)
        if sym_info is None:
            return None
        tick = mt5.symbol_info_tick(sym_info.name)
        if not tick:
            return None
        return tick.ask if action == "BUY" else tick.bid

    def place_market_order(
        self, signal: dict, lot: float, tp: float
    ) -> int | None:
        log.info(f"[DEBUG] place_market_order ENTRÉE sym={signal['symbol']} action={signal['action']} lot={lot} tp={tp}")
        sym = self._sym(signal["symbol"])
        if not sym:
            log.error(f"[DEBUG] sym=None pour {signal['symbol']}")
            return None
        action = signal["action"]
        tick = mt5.symbol_info_tick(sym.name)
        if not tick:
            log.error(f"Pas de tick pour {sym.name}")
            return None
        price = tick.ask if action == "BUY" else tick.bid
        log.info(f"[DEBUG] prix={price} filling_mode={sym.filling_mode}")
        otype = (
            mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        )

        # Essayer tous les modes de remplissage
        filling_modes = []
        filling = sym.filling_mode
        if filling & SYMBOL_FILLING_FOK:
            filling_modes.append(ORDER_FILLING_FOK)
        if filling & SYMBOL_FILLING_IOC:
            filling_modes.append(ORDER_FILLING_IOC)
        filling_modes.append(ORDER_FILLING_RETURN)
        log.info(f"[DEBUG] filling_modes à tester: {filling_modes}")

        for fill_mode in filling_modes:
            log.info(f"[DEBUG] Tentative filling={fill_mode} sym={sym.name} vol={lot} price={price} sl={round(signal['sl'], sym.digits)} tp={round(tp, sym.digits)}")
            result = mt5.order_send(
                {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": sym.name,
                    "volume": lot,
                    "type": otype,
                    "price": price,
                    "sl": round(signal["sl"], sym.digits),
                    "tp": round(tp, sym.digits),
                    "deviation": SLIPPAGE,
                    "magic": MAGIC_NUMBER,
                    "comment": f"TG-market {datetime.now():%H:%M}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": fill_mode,
                }
            )
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log.info(
                    f"MARKET {action} {sym.name} lot={lot} @{price} "
                    f"ticket#{result.order} filling={fill_mode}"
                )
                return result.order
            log.warning(
                f"Market échoué filling={fill_mode} | "
                f"retcode={result.retcode if result else 'N/A'} "
                f"comment={result.comment if result else 'N/A'}"
            )

        log.error(
            f"Market TOUS les fillings échoués | "
            f"sym={sym.name} lot={lot} price={price}"
        )
        return None

    def place_limit_order(
        self, signal: dict, lot: float, price: float,
        tp: float, expiry: datetime
    ) -> int | None:
        sym = self._sym(signal["symbol"])
        if not sym:
            return None
        action = signal["action"]
        if action == "BUY" and tp <= price:
            return None
        if action == "SELL" and tp >= price:
            return None
        otype = (
            mt5.ORDER_TYPE_BUY_LIMIT
            if action == "BUY"
            else mt5.ORDER_TYPE_SELL_LIMIT
        )
        filling = self._get_filling(sym)
        result = mt5.order_send(
            {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": sym.name,
                "volume": lot,
                "type": otype,
                "price": round(price, sym.digits),
                "sl": round(signal["sl"], sym.digits),
                "tp": round(tp, sym.digits),
                "deviation": SLIPPAGE,
                "magic": MAGIC_NUMBER,
                "comment": f"TG-limit {datetime.now():%H:%M}",
                "type_time": mt5.ORDER_TIME_SPECIFIED,
                "expiration": int(expiry.timestamp()),
                "type_filling": filling,
            }
        )
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                f"LIMIT {action} {sym.name} lot={lot} @{price} "
                f"TP={tp} order#{result.order}"
            )
            return result.order
        log.error(
            f"Limit échoué @{price} | "
            f"retcode={result.retcode if result else 'N/A'} "
            f"| comment={result.comment if result else 'N/A'} "
            f"| sym={sym.name} lot={lot} filling={filling}"
        )
        return None

    def cancel_order(self, order_ticket: int) -> bool:
        result = mt5.order_send(
            {"action": mt5.TRADE_ACTION_REMOVE, "order": order_ticket}
        )
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"{'OK' if ok else 'FAIL'} Annulation #{order_ticket}")
        return ok

    def close_position(self, ticket: int, comment: str = "close") -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        cprice = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        ctype = (
            mt5.ORDER_TYPE_SELL
            if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        filling = self._get_filling(mt5.symbol_info(pos.symbol))
        result = mt5.order_send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": ctype,
                "position": ticket,
                "price": cprice,
                "deviation": SLIPPAGE,
                "magic": MAGIC_NUMBER,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
        )
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        log.info(
            f"{'OK' if ok else 'FAIL'} Fermeture #{ticket} "
            f"({comment}) P&L={pos.profit:.2f}"
        )
        return ok

    def modify_sl(
        self, ticket: int, new_sl: float, label: str = ""
    ) -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        sym = mt5.symbol_info(pos.symbol)
        if sym is None:
            return False
        result = mt5.order_send(
            {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": pos.symbol,
                "position": ticket,
                "sl": round(new_sl, sym.digits),
                "tp": pos.tp,
            }
        )
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info(f"SL modifié #{ticket} → {new_sl} {label}")
        return ok

    def update_sl_all(self, new_sl: float):
        updated = 0
        positions = mt5.positions_get()
        if positions:
            for pos in positions:
                if pos.magic != MAGIC_NUMBER:
                    continue
                sym = mt5.symbol_info(pos.symbol)
                if not sym:
                    continue
                result = mt5.order_send(
                    {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": pos.symbol,
                        "position": pos.ticket,
                        "sl": round(new_sl, sym.digits),
                        "tp": pos.tp,
                    }
                )
                ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
                if ok:
                    updated += 1
        orders = mt5.orders_get()
        if orders:
            for order in orders:
                if order.magic != MAGIC_NUMBER:
                    continue
                sym = mt5.symbol_info(order.symbol)
                if not sym:
                    continue
                result = mt5.order_send(
                    {
                        "action": mt5.TRADE_ACTION_MODIFY,
                        "order": order.ticket,
                        "price": order.price_open,
                        "sl": round(new_sl, sym.digits),
                        "tp": order.tp,
                        "type_time": order.type_time,
                        "expiration": order.time_expiration,
                    }
                )
                ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
                if ok:
                    updated += 1
        log.info(
            f"SL MOVE appliqué sur {updated} pos/ordres → SL={new_sl}"
        )

    def close_all(self, symbol: str | None = None):
        positions = (
            mt5.positions_get(symbol=symbol)
            if symbol
            else mt5.positions_get()
        )
        if not positions:
            return
        for pos in positions:
            if pos.magic == MAGIC_NUMBER:
                self.close_position(pos.ticket, comment="close-all")


# =============================================================
# CONFLIT & EXÉCUTION
# =============================================================
def check_conflict(signal: dict, bridge: MT5Bridge, manager) -> bool:
    if DEMO_MODE:
        return False
    symbol = signal["symbol"]
    new_action = signal["action"]
    opposite = "SELL" if new_action == "BUY" else "BUY"
    conflict = False

    positions = mt5.positions_get()
    if positions:
        for pos in positions:
            if pos.magic != MAGIC_NUMBER:
                continue
            pos_dir = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
            if pos_dir == opposite:
                conflict = True
                break

    if not conflict:
        with manager._lock:
            for entry in manager.active:
                if (
                    entry["signal"]["symbol"] == symbol
                    and entry["signal"]["action"] == opposite
                ):
                    conflict = True
                    break

    if not conflict:
        return False

    log.warning(
        f"CONFLIT {symbol} : entrant={new_action} existant={opposite}"
    )
    with manager._lock:
        to_remove = []
        for entry in manager.active:
            if entry["signal"]["symbol"] != symbol:
                continue
            for o in entry["orders"]:
                bridge.cancel_order(o["order"])
            to_remove.append(entry)
        for e in to_remove:
            if e in manager.active:
                manager.active.remove(e)
    bridge.close_all(symbol=symbol)
    return True


def execute_signal(signal: dict, bridge: MT5Bridge, manager, tracker):
    action = signal["action"]
    symbol = signal["symbol"]
    zone_low = signal["zone_low"]
    zone_mid = signal["zone_mid"]
    zone_high = signal["zone_high"]

    all_tps = signal["tps"]
    tp1 = all_tps[0]  # first TP (breakeven threshold)
    tp_final = all_tps[-1]  # last TP (trailing target)
    sl = signal["sl"]
    expiry = datetime.now() + timedelta(minutes=ORDER_EXPIRY_MIN)

    if check_conflict(signal, bridge, manager):
        return

    sym_info = bridge._sym(symbol)
    if sym_info is None:
        return

    current = bridge.current_price(sym_info.name, action)
    if current is None:
        return

    in_zone = zone_low <= current <= zone_high
    canal = signal.get("source_channel", "Inconnu")
    mode = "DEMO" if DEMO_MODE else "LIVE"
    log.info("=" * 55)
    log.info(f"SIGNAL [{mode}] {action} {symbol} | Canal: {canal}")
    log.info(
        f"Zone [{zone_low} — {zone_mid} — {zone_high}] | Prix={current}"
    )
    log.info(
        f"{'DANS la zone → CAS 1' if in_zone else 'HORS zone → CAS 2'}"
    )
    log.info(f"TPs={all_tps} ({len(all_tps)}) | SL={sl}")
    log.info("=" * 55)

    # Distribute lot across TPs
    n_tps = len(all_tps)
    lot_per_tp = round(LOT_SIZE / n_tps, 2)
    if lot_per_tp < 0.01:
        lot_per_tp = 0.01
    log.info(f"Lot total={LOT_SIZE} → {lot_per_tp} × {n_tps} positions")

    orders, tickets = [], []

    if in_zone:
        # CAS 1: market order per TP
        for i, tp_val in enumerate(all_tps):
            tp_name = f"TP{i+1}"
            log.info(f"[DEBUG] Market {tp_name} lot={lot_per_tp} tp={tp_val}")
            try:
                t = bridge.place_market_order(signal, lot_per_tp, tp=tp_val)
            except Exception as e:
                log.error(f"[DEBUG] EXCEPTION {tp_name}: {e}")
                t = None
            if t:
                tickets.append(
                    {
                        "ticket": t,
                        "lot": lot_per_tp,
                        "role": f"market_tp{i+1}",
                        "entry_price": current,
                        "signal_tp1": tp1,
                        "tp_index": i,
                        "tp_target": tp_val,
                        "tp1": tp1,
                        "tp2": tp_final,
                        "sl_step": 0,
                        "trail_active": False,
                    }
                )
            log.info(f"CAS 1 → {tp_name} MARKET {lot_per_tp} TP={tp_val}")

        # Also place a limit at zone edge (averaging if retrace)
        limit_price = zone_high if action == "SELL" else zone_low
        o1 = bridge.place_limit_order(
            signal, lot_per_tp, limit_price, tp_final, expiry
        )
        if o1:
            orders.append(
                {
                    "order": o1,
                    "lot": lot_per_tp,
                    "price": limit_price,
                    "role": "limit_cas1",
                    "signal_tp1": tp1,
                    "tp_index": n_tps - 1,
                    "tp_target": tp_final,
                    "tp1": tp1,
                    "tp2": tp_final,
                    "sl_step": 0,
                    "trail_active": False,
                }
            )
    else:
        # CAS 2: limit orders per TP
        if action == "BUY":
            dist = zone_low - sl
            pa = round(zone_low - dist / 3, sym_info.digits)
            edge_prices = [zone_high, pa]
        else:
            dist = sl - zone_high
            pa = round(zone_high + dist / 3, sym_info.digits)
            edge_prices = [zone_low, pa]

        for ep_idx, edge_price in enumerate(edge_prices):
            role = "limit_high" if ep_idx == 0 else "limit_low"
            for i, tp_val in enumerate(all_tps):
                tp_name = f"TP{i+1}"
                o = bridge.place_limit_order(
                    signal, lot_per_tp, edge_price, tp_val, expiry
                )
                if o:
                    orders.append(
                        {
                            "order": o,
                            "lot": lot_per_tp,
                            "price": edge_price,
                            "role": f"{role}_tp{i+1}",
                            "signal_tp1": tp1,
                            "tp_index": i,
                            "tp_target": tp_val,
                            "tp1": tp1,
                            "tp2": tp_final,
                            "sl_step": 0,
                            "trail_active": False,
                        }
                    )
                log.info(
                    f"CAS 2 → {tp_name} LIMIT {lot_per_tp} @{edge_price} TP={tp_val}"
                )

    if not orders and not tickets:
        log.error("Aucun ordre placé.")
        return

    entry = {
        "signal": signal,
        "orders": orders,
        "tickets": tickets,
        "expiry": expiry,
        "_open_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    manager.register(entry)
    tracker.log_trade_open(entry)

    # Log to Supabase
    if _supa_connected and _supa:
        ticket_ids = [t["ticket"] for t in tickets]
        supa_trade_id = _supa.log_trade_open(
            signal=signal,
            entry_price=current,
            lot_size=LOT_SIZE,
            tickets=ticket_ids,
        )
        entry["_supa_trade_id"] = supa_trade_id

# =============================================================
# TRADE MANAGER
# =============================================================
class TradeManager:

    def __init__(self, bridge: MT5Bridge, reporter: TradeReporter):
        self.bridge = bridge
        self.reporter = reporter
        self.active = []
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def register(self, entry: dict):
        with self._lock:
            self.active.append(entry)
        sig = entry["signal"]
        canal = sig.get("source_channel", "Inconnu")
        mode = "DEMO" if DEMO_MODE else "LIVE"
        log.info(
            f"TradeManager [{mode}]: {sig['action']} {sig['symbol']} "
            f"Canal: {canal} | {len(entry['orders'])} ordres"
        )

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            time.sleep(10)
            try:
                self._check_all()
            except Exception as exc:
                log.error(f"TradeManager erreur: {exc}")

    def _get_last_pnl(self, ticket: int, symbol: str) -> float:
        since = datetime.now() - timedelta(hours=24)
        deals = mt5.history_deals_get(since, datetime.now(), group=symbol)
        if deals:
            for deal in reversed(deals):
                if deal.position_id == ticket:
                    return deal.profit
        return 0.0

    def _get_pos(self, ticket: int):
        r = mt5.positions_get(ticket=ticket)
        return r[0] if r else None

    def _resolve_order(self, order_ticket: int, symbol: str):
        since = datetime.now() - timedelta(hours=24)
        deals = mt5.history_deals_get(since, datetime.now(), group=symbol)
        if not deals:
            return None
        for deal in deals:
            if (
                deal.order == order_ticket
                and deal.entry == mt5.DEAL_ENTRY_IN
            ):
                positions = mt5.positions_get(ticket=deal.position_id)
                if positions:
                    return positions[0]
        return None

    def _schedule_report(self, coro):
        if self.reporter._loop:
            asyncio.run_coroutine_threadsafe(coro, self.reporter._loop)
        else:
            log.warning("Reporter non initialisé, rapport ignoré")

    def _check_all(self):
        now = datetime.now()
        to_remove = []

        with self._lock:
            entries_snapshot = list(self.active)

        for entry in entries_snapshot:
            sig = entry["signal"]
            symbol = sig["symbol"]
            action = sig["action"]

            # Resolve pending limit orders → tickets
            still_pending = []
            with self._lock:
                for o in entry["orders"]:
                    pos = self._resolve_order(o["order"], symbol)
                    if pos:
                        tk = {
                            "ticket": pos.ticket,
                            "lot": o["lot"],
                            "role": o["role"],
                            "entry_price": pos.price_open,
                            "signal_tp1": o.get("signal_tp1", 0),
                            "tp_index": o.get("tp_index", 0),
                            "tp_target": o.get("tp_target", 0),
                            "tp1": o["tp1"],
                            "tp2": o["tp2"],
                            "sl_step": 0,
                            "trail_active": False,
                        }
                        entry["tickets"].append(tk)
                        log.info(
                            f"Ordre #{o['order']} rempli → "
                            f"ticket={pos.ticket} @{pos.price_open}"
                        )
                    elif now > entry["expiry"]:
                        self.bridge.cancel_order(o["order"])
                    else:
                        still_pending.append(o)
                entry["orders"] = still_pending

            active_tks = [
                t for t in entry["tickets"] if self._get_pos(t["ticket"])
            ]
            if not entry["orders"] and not active_tks:
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)
                continue

            sym_info = self.bridge._sym(symbol)
            if sym_info is None:
                continue
            tick = mt5.symbol_info_tick(sym_info.name)
            if tick is None:
                continue
            current = tick.bid if action == "BUY" else tick.ask

            # Check which TPs have been hit (positions closed)
            tp_indices_closed = set()
            for t in entry["tickets"]:
                pos = self._get_pos(t["ticket"])
                if pos is None and not t.get("_reported"):
                    t["_reported"] = True
                    pnl = self._get_last_pnl(t["ticket"], symbol)
                    t["_last_pnl"] = pnl
                    tp_idx = t.get("tp_index", -1)
                    tp_val = t.get("tp_target", 0)
                    tp_indices_closed.add(tp_idx)
                    if pnl >= 0:
                        self._schedule_report(
                            self.reporter.on_tp_reached(
                                t["ticket"],
                                sig,
                                f"TP{tp_idx+1}",
                                tp_val,
                                pnl,
                            )
                        )
                        if _supa_connected and _supa:
                            supa_id = entry.get("_supa_trade_id")
                            if supa_id:
                                _supa.log_tp_hit(supa_id, f"TP{tp_idx+1}", tp_val, pnl)
                    else:
                        self._schedule_report(
                            self.reporter.on_sl_hit(
                                t["ticket"], sig, pnl
                            )
                        )
                        if _supa_connected and _supa:
                            supa_id = entry.get("_supa_trade_id")
                            if supa_id:
                                _supa.log_sl_hit(supa_id, pnl)

            # After first TP hit: move SL to breakeven + activate trailing
            if tp_indices_closed and 0 in tp_indices_closed:
                for t in entry["tickets"]:
                    if self._get_pos(t["ticket"]) and t.get("sl_step", 0) == 0:
                        ep = t.get("entry_price", 0)
                        self.bridge.modify_sl(
                            t["ticket"], ep, label="[BE after TP1]"
                        )
                        t["sl_step"] = 1
                        log.info(
                            f"BE activé #{t['ticket']} → SL={ep}"
                        )

            # After OPEN_TRAIL_AFTER_TP TPs: activate trailing
            min_trail_tp = OPEN_TRAIL_AFTER_TP - 1
            if any(idx >= min_trail_tp for idx in tp_indices_closed):
                for t in entry["tickets"]:
                    if self._get_pos(t["ticket"]) and not t.get("trail_active"):
                        t["trail_active"] = True
                        t["sl_step"] = 1
                        log.info(
                            f"Trail activé #{t['ticket']}"
                        )

            # Trailing SL update for active positions
            for t in entry["tickets"]:
                if not t.get("trail_active"):
                    continue
                pos = self._get_pos(t["ticket"])
                if not pos:
                    continue
                sym2 = mt5.symbol_info(pos.symbol)
                if sym2 is None:
                    continue
                d = sym2.digits
                pv = (
                    10 * sym2.point
                    if d in (3, 5)
                    else sym2.point
                )
                gap = TRAIL_POINTS * pv
                if action == "BUY":
                    nsl = current - gap
                    if nsl > pos.sl:
                        self.bridge.modify_sl(
                            t["ticket"],
                            nsl,
                            label="[Trail BUY]",
                        )
                else:
                    nsl = current + gap
                    if nsl < pos.sl or pos.sl == 0:
                        self.bridge.modify_sl(
                            t["ticket"],
                            nsl,
                            label="[Trail SELL]",
                        )

            # Check if trade fully closed
            active_tks = [
                t
                for t in entry["tickets"]
                if self._get_pos(t["ticket"])
            ]
            if not entry["orders"] and not active_tks:
                total_pnl = sum(
                    t.get("_last_pnl", 0.0) for t in entry["tickets"]
                )
                canal = sig.get("source_channel", "Inconnu")
                log.info(
                    f"Trade terminé ({symbol}) | Canal: {canal} "
                    f"| P&L total: {total_pnl:+.2f}"
                )
                self._schedule_report(
                    self.reporter.on_trade_closed(entry, total_pnl)
                )
                # Log dans le tracker
                if hasattr(self, "tracker") and self.tracker:
                    self.tracker.log_trade_close(entry, total_pnl)
                # Log dans Supabase
                if _supa_connected and _supa:
                    supa_id = entry.get("_supa_trade_id")
                    if supa_id:
                        result_str = "WIN" if total_pnl > 0 else ("BE" if total_pnl == 0 else "LOSS")
                        open_date = entry.get("_open_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        try:
                            duree = (datetime.now() - datetime.strptime(open_date, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                        except Exception:
                            duree = 0
                        _supa.log_trade_close(supa_id, result_str, total_pnl, duree)
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)


# =============================================================
# SHUTDOWN TIMER
# =============================================================
async def shutdown_watcher(reporter, tracker, bridge, manager, news_mgr):
    """Surveille le temps restant et envoie le rapport avant fermeture."""
    global _report_sent

    if RUNTIME_MINUTES <= 0:
        log.info("[SHUTDOWN] Pas de durée définie → pas de timer")
        return

    end_time = START_TIME + timedelta(minutes=RUNTIME_MINUTES)
    log.info(
        f"[SHUTDOWN] Session de {RUNTIME_MINUTES} min → "
        f"fin prévue à {end_time:%H:%M:%S}"
    )

    while not _shutdown_event.is_set():
        now = datetime.now()
        remaining = (end_time - now).total_seconds() / 60

        if remaining <= SHUTDOWN_MARGIN_MIN and not _report_sent:
            _report_sent = True
            log.info(
                f"[SHUTDOWN] Fin dans {remaining:.0f} min → "
                f"envoi du rapport final"
            )
            await tracker.send_final_report(reporter)
            log.info("[SHUTDOWN] Rapport envoyé. Le bot continue...")
            break

        # Heartbeat toutes les 5 minutes
        if int(remaining) % 5 == 0 and remaining > SHUTDOWN_MARGIN_MIN:
            log.info(
                f"[SHUTDOWN] Reste {remaining:.0f} min"
            )

        await asyncio.sleep(30)


def sigterm_handler():
    """Appelé quand SIGTERM est reçu (timeout GitHub Actions)."""
    global _report_sent
    log.info("[SHUTDOWN] SIGTERM reçu → arrêt propre")
    _shutdown_event.set()
    if not _report_sent:
        _report_sent = True
        log.info("[SHUTDOWN] Envoi forcé du rapport...")


# =============================================================
# MAIN
# =============================================================
async def main():
    global _report_sent

    parser = SignalParser()
    bridge = MT5Bridge()
    reporter = TradeReporter()
    tracker = PerformanceTracker()
    manager = None

    if not bridge.connect():
        log.critical("Bot arrêté — corrigez MT5 puis relancez.")
        return

    manager = TradeManager(bridge, reporter)
    manager.tracker = tracker  # ← Attacher le tracker au manager

    news_mgr = NewsManager(bridge)
    news_mgr.set_manager(manager)

    client = TelegramClient("session_trading", API_ID, API_HASH)
    await client.start()
    log.info("Telegram connecté.")

    await reporter.set_telegram_client(client)

    # SIGTERM handler
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, sigterm_handler)
        log.info("[SHUTDOWN] SIGTERM handler installé")
    except NotImplementedError:
        # Windows ne supporte pas add_signal_handler
        signal.signal(signal.SIGTERM, lambda s, f: sigterm_handler())
        log.info("[SHUTDOWN] SIGTERM handler installé (fallback)")

    # ------------------------------------------------------------------
    # SUPABASE SESSION START
    # ------------------------------------------------------------------
    channel_list = [ch for _, ch in channel_names if ch]
    if _supa_connected and _supa:
        _supa.start_session(
            runtime_minutes=RUNTIME_MINUTES,
            channels=channel_list,
            lot_size=LOT_SIZE,
            mode="DEMO" if DEMO_MODE else "LIVE",
        )

    chats = []
    channel_names = [
        ("TG_CHANNEL", CHANNEL_NAME),
        ("TG_CHANNEL_2", CHANNEL_NAME_2),
        ("TG_CHANNEL_3", CHANNEL_NAME_3),
        ("TG_CHANNEL_4", CHANNEL_NAME_4),
    ]
    entity_to_name = {}

    for env_name, ch_value in channel_names:
        if not ch_value:
            continue
        try:
            entity = await client.get_entity(ch_value)
            title = getattr(entity, "title", ch_value)
            chats.append(entity)
            entity_to_name[entity.id] = title
            log.info(f"Canal : {title} ({env_name}={ch_value})")
        except Exception as e:
            log.warning(f"Canal introuvable ({env_name}={ch_value}) : {e}")

    @client.on(events.NewMessage(chats=chats))
    async def handler(event):
        text = event.message.text or ""
        chat = await event.get_chat()
        canal_name = entity_to_name.get(
            chat.id, getattr(chat, "title", "inconnu")
        )

        if is_spam(text):
            return

        log.info(
            f"[{canal_name}] {text[:150].replace(chr(10), ' | ')}"
        )

        signal_data = parser.parse(text)
        if signal_data is None:
            return

        signal_data["source_channel"] = canal_name

        if signal_data["type"] == "CLOSE":
            bridge.close_all(symbol=signal_data.get("symbol"))
            return

        elif signal_data["type"] == "SL_MOVE":
            log.info(
                f"SL MOVE reçu → nouveau SL={signal_data['new_sl']}"
            )
            bridge.update_sl_all(signal_data["new_sl"])
            return

        elif signal_data["type"] == "TRADE":
            blocked, desc = in_blocked_window()
            if blocked:
                log.info(f"[TIME] Signal ignoré — {desc}")
                return
            if NEWS_ENABLED and news_mgr.is_blocked():
                log.info("[NEWS] Signal ignoré — protection news")
                return
            execute_signal(signal_data, bridge, manager, tracker)
            with manager._lock:
                for entry in manager.active:
                    if entry["signal"] is signal_data:
                        await reporter.on_order_opened(entry)
                        break

    # Banner
    mode = "🧪 DEMO" if DEMO_MODE else "💰 LIVE"
    log.info("=" * 55)
    log.info(f" TRADINGBOT V4.0 — {mode}")
    log.info(f" Canaux surveillés : {len(chats)}")
    for env_name, ch_value in channel_names:
        if ch_value:
            log.info(f"  {env_name} : {ch_value}")
    if REPORT_CHANNEL:
        log.info(f" Canal de rapport : {REPORT_CHANNEL}")
    log.info(f" Lot : {LOT_SIZE}")
    log.info(f" Trail SL : {TRAIL_POINTS} pts")
    log.info(f" News filter : {'ON' if NEWS_ENABLED else 'OFF'}")
    log.info(f" Time filter : {'ON' if TIME_FILTER_ENABLED else 'OFF'}")
    if RUNTIME_MINUTES > 0:
        end = START_TIME + timedelta(minutes=RUNTIME_MINUTES)
        log.info(f" Session : {RUNTIME_MINUTES} min (fin {end:%H:%M})")
    log.info(f" Performance : CSV + rapports activés")
    log.info("=" * 55)

    try:
        # Lancer le timer de shutdown en arrière-plan
        shutdown_task = asyncio.create_task(
            shutdown_watcher(reporter, tracker, bridge, manager, news_mgr)
        )
        await client.run_until_disconnected()
    finally:
        # Rapport final si pas encore envoyé
        if not _report_sent and reporter._tg_client:
            _report_sent = True
            log.info("[SHUTDOWN] Envoi du rapport final (finally)...")
            try:
                await tracker.send_final_report(reporter)
            except Exception as e:
                log.error(f"[SHUTDOWN] Erreur rapport final : {e}")

        if _supa_connected and _supa:
            total_t = len(tracker._trades_cache)
            total_p = sum(t.get("pnl", 0) for t in tracker._trades_cache)
            _supa.end_session(total_t, total_p)
        if manager:
            manager.stop()
        if news_mgr:
            news_mgr.stop()
        bridge.disconnect()
        log.info("[SHUTDOWN] Bot arrêté proprement.")


if __name__ == "__main__":
    asyncio.run(main())
