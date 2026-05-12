"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 4.3.2 — diagnostic logs + TradeReporter fix
=============================================================
 Changements v4.3.2 (2026-05-05) :
 - FIX: TradeReporter forward reference (type hint string)
 - NEW: Logs diagnostic pour signaux rejetés silencieusement (symbole/prix)

 Changements v4.1 :
 - FIX: _parse_main() group(4) optionnel (évite TypeError)
 - FIX: datetime naive vs aware (duree_min maintenant correct)
 - FIX: threading.Lock au lieu de asyncio.Lock (thread-safe)
 - FIX: is_spam() appelé AVANT les parsers spécialisés
 - NEW: Parser V5 — reconstruit de zéro (6 formats supportés)
 - NEW: CAS 1 → 1 market (TP2) + 1 limit (TP_final)
 - NEW: CAS 1 TP2 hit → annule limit ou active BE+trailing
 - DEL: Filtre horaire désactivé temporairement
"""

# ── Auto-install des dépendances manquantes ──
import subprocess, sys
_deps = {"dotenv": "python-dotenv", "telethon": "telethon", "MetaTrader5": "MetaTrader5"}
for _mod, _pkg in _deps.items():
    try:
        __import__(_mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import asyncio
import re
import logging
import time
import json
import urllib.request
import signal
import os
import threading  # FIX: thread-safe lock pour to_thread
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from telethon import TelegramClient, events
import MetaTrader5 as mt5

# Constantes de filling mode
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

MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "")
MT5_PATH     = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")

MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20250226"))
SLIPPAGE = int(os.getenv("SLIPPAGE", "20"))
ORDER_EXPIRY_MIN = int(os.getenv("ORDER_EXPIRY_MINUTES", "240"))
TRAIL_POINTS = float(os.getenv("TRAIL_POINTS", "150"))
LOT_SIZE = float(os.getenv("LOT_TOTAL", "0.01"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "6"))
MAX_SPREAD_POINTS = float(os.getenv("MAX_SPREAD_POINTS", "50"))

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

NEWS_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "true").lower() == "true"
NEWS_BLOCK_MIN = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK", "15"))
NEWS_CLOSE_MIN = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE", "5"))
NEWS_AFTER_MIN = int(os.getenv("NEWS_WINDOW_AFTER", "15"))

# ⚠️ FILTRE HORAIRE DÉSACTIVÉ TEMPORAIREMENT (v4.2-patch)
# TIME_FILTER_ENABLED = os.getenv("TIME_FILTER_ENABLED", "true").lower() == "true"
TIME_FILTER_ENABLED = False

# TP: Open config
OPEN_TP_RR_RATIOS = [float(x) for x in os.getenv("OPEN_TP_RR_RATIOS", "1.0,2.0,3.0").split(",")]
OPEN_TP_COUNT = int(os.getenv("OPEN_TP_COUNT", "3"))
OPEN_TRAIL_AFTER_TP = int(os.getenv("OPEN_TRAIL_AFTER_TP", "1"))

RUNTIME_MINUTES = int(os.getenv("RUNTIME_MINUTES", "0"))
SHUTDOWN_MARGIN_MIN = 5

START_TIME = datetime.now(timezone.utc)
_shutdown_event = asyncio.Event()
_report_event = asyncio.Event()


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
# ⚠️ FILTRE HORAIRE DÉSACTIVÉ TEMPORAIREMENT (v4.2-patch)
# BLOCKED_WINDOWS = _parse_blocked_windows(_raw_windows)
BLOCKED_WINDOWS = []

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
    log.warning("supabase_logger non trouvé — pas de log distant")

console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
console_handler.addFilter(OrderFilter())
log.addHandler(console_handler)

# ------------------------------------------------------------------
# FILTRE MESSAGES NON-TRADING (importé depuis signal_parser.py)
# ------------------------------------------------------------------
# is_spam et SignalParser sont définis directement dans ce fichier

# ------------------------------------------------------------------
# GESTION FENÊTRES HORAIRES BLOQUÉES
# ------------------------------------------------------------------
def in_blocked_window() -> tuple[bool, str]:
    # ⚠️ FILTRE HORAIRE DÉSACTIVÉ TEMPORAIREMENT (v4.2-patch)
    # if not TIME_FILTER_ENABLED:
    #     return False, ""
    # now = datetime.now(timezone.utc)
    # now_minutes = now.hour * 60 + now.minute
    # for (h1, m1, h2, m2) in BLOCKED_WINDOWS:
    #     start = h1 * 60 + m1
    #     end = h2 * 60 + m2
    #     if start <= now_minutes < end:
    #         desc = f"{h1:02d}h{m1:02d}-{h2:02d}h{m2:02d} UTC"
    #         return True, desc
    return False, ""


# =============================================================
# PERFORMANCE TRACKER
# =============================================================
class PerformanceTracker:

    def __init__(self):
        self._trades_cache = []
        self._report_sent = False

    def log_trade_open(self, entry):
        sig = entry["signal"]
        now = datetime.now(timezone.utc)
        row = {
            "canal": sig.get("source_channel", "Inconnu"),
            "symbol": sig["symbol"],
            "action": sig["action"],
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
        now = datetime.now(timezone.utc)
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
                break

    def format_session_summary(self) -> str:
        if not self._trades_cache:
            return "📊 Aucun trade cette session."

        wins = sum(1 for t in self._trades_cache if t["result"] == "WIN")
        losses = sum(1 for t in self._trades_cache if t["result"] == "LOSS")
        be = sum(1 for t in self._trades_cache if t["result"] == "BE")
        still_open = sum(1 for t in self._trades_cache if t["result"] == "OPEN")
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

    async def send_final_report(self, reporter):
        if self._report_sent:
            return
        self._report_sent = True
        log.info("[PERF] Envoi du rapport final...")
        summary = self.format_session_summary()
        await reporter.send_tg(summary)


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
        # v4.2: Utilise asyncio au lieu de threading
        self._task = None

    def set_manager(self, manager):
        self.manager = manager

    def is_blocked(self) -> bool:
        return self._blocked

    async def start(self):
        """Démarre la boucle de news en tant que tâche asyncio."""
        self._task = asyncio.create_task(self._loop_async())

    async def _loop_async(self):
        while not self._stop:
            try:
                await asyncio.to_thread(self._fetch_news)
                await asyncio.to_thread(self._check_news)
            except Exception as e:
                log.error(f"NewsManager erreur: {e}")
            await asyncio.sleep(1800)  # 30 minutes

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
                    log.info(f"[NEWS] {news.get('title', '?')} terminé → reprise")
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
            for entry in list(self.manager.active):
                for o in entry.get("orders", []):
                    self.bridge.cancel_order(o["order"])
                entry["orders"] = []
            self.bridge.close_all()

    def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()


# =============================================================
# SIGNAL PARSER V5 — intégré directement (pas d'import externe)
# =============================================================

SYMBOL_MAP = {
    "GOLD": "XAUUSD",
    "XAU/USD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD",
    "XAG/USD": "XAGUSD",
    "XAGUSD": "XAGUSD",
    "OIL": "USOIL",
    "USOIL": "USOIL",
    "BTC": "BTCUSD",
    "BTC/USD": "BTCUSD",
    "BITCOIN": "BTCUSD",
    "BTCUSD": "BTCUSD",
}

RE_SYMBOL = re.compile(
    r"(XAU/?USD|GOLD|XAG/?USD|SILVER|USOIL|OIL|BTC/?USD|BITCOIN|BTCUSD)",
    re.IGNORECASE,
)
RE_ACTION = re.compile(r"\b(BUY|SELL)\b", re.IGNORECASE)
RE_NUM = r"([\d]+(?:\.\d+)?)"
RE_RANGE = re.compile(rf"{RE_NUM}\s*[-/ ]\s*{RE_NUM}")

EXCLUDE_KEYWORDS_PARSER = [
    "tp hit", "tp1 hit", "tp2 hit", "tp3 hit", "all tp hit",
    "mission acomplished", "boom boom boom",
    "my signal are on fire", "pips profit", "pips gain",
    "closed at", "exit at", "sl hit", "stopped",
    "secured", "hit target", "be safe", "good luck",
    "market update", "analysis",
    "are you in big loss", "contact",
    "use proper money management", "consistency",
]
SPAM_STANDALONE = ["target", "running"]


def is_spam(text: str) -> bool:
    """Détecte les messages non-trading."""
    low = text.lower()
    lines = low.split("\n")
    for kw in EXCLUDE_KEYWORDS_PARSER:
        if kw in low:
            return True
    for kw in SPAM_STANDALONE:
        for line in lines:
            stripped = line.strip().strip("📍🎯📊📈📉❌✅🔴🟢⚪")
            if stripped == kw or stripped == kw + ":":
                return True
    return False


def _resolve_symbol(raw: str) -> str:
    clean = raw.upper().strip().replace(" ", "")
    return SYMBOL_MAP.get(clean, clean)


def _parse_range(text: str) -> tuple[float, float] | None:
    m = RE_RANGE.search(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def _extract_tps(text: str) -> list[float]:
    tps = []
    for m in re.finditer(r"TP\s*\d+\s*[:.]\s*" + RE_NUM, text, re.IGNORECASE):
        tps.append(float(m.group(1)))
    if tps:
        return tps
    for m in re.finditer(r"TAKE\s+PROFIT\s*[.:]?\s*" + RE_NUM, text, re.IGNORECASE):
        tps.append(float(m.group(1)))
    if tps:
        return tps
    for m in re.finditer(
        r"^\s*TP\s+" + RE_NUM + r"(?:\s*[✅☑️✔️🎯]|\s+CONFIRM|\s+HIT)?\s*$",
        text, re.IGNORECASE | re.MULTILINE
    ):
        tps.append(float(m.group(1)))
    if tps:
        return tps
    for m in re.finditer(
        r"TP\s*[¹²³⁴⁵⁶⁷⁸⁹⁰ⁿ]\s*" + RE_NUM,
        text, re.IGNORECASE
    ):
        tps.append(float(m.group(1)))
    return tps


def _extract_sl(text: str) -> float | None:
    m = re.search(
        r"(?:STOP\s*LOSS|Stop\s+Loss)\s*(?:\(\s*SL\s*\))?\s*[.:]?\s*" + RE_NUM,
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))
    m = re.search(r"SL\s*[_:.]?\s*" + RE_NUM, text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _extract_symbol(text: str) -> str | None:
    m = RE_SYMBOL.search(text)
    return _resolve_symbol(m.group(1)) if m else None


def _extract_action(text: str) -> str | None:
    m = RE_ACTION.search(text)
    return m.group(1).upper() if m else None


def _detect_action_from_tps(zone_low: float, zone_high: float, tps: list[float]) -> str:
    avg_entry = (zone_low + zone_high) / 2
    avg_tp = sum(tps) / len(tps)
    return "BUY" if avg_tp > avg_entry else "SELL"


class SignalParser:

    def parse(self, text: str) -> dict | None:
        if not text or not text.strip():
            return None
        if is_spam(text):
            log.debug(f"[SPAM] {text[:60].replace(chr(10), ' ')}")
            return None
        result = self._parse_close(text)
        if result:
            return result
        result = self._parse_sl_move(text)
        if result:
            return result
        result = self._parse_trade(text)
        if result:
            return result
        return None

    def _parse_close(self, text: str) -> dict | None:
        m = re.search(r"close\s+(all|[A-Z]{3,10})", text, re.IGNORECASE)
        if not m:
            return None
        target = m.group(1).upper()
        return {"type": "CLOSE", "symbol": None if target == "ALL" else _resolve_symbol(target), "close_all": target == "ALL"}

    def _parse_sl_move(self, text: str) -> dict | None:
        m = re.search(
            r"(?:SL\s*MOVE|MOVE\s*SL|New\s*SL|SL\s*→|SL\s*moved?\s*to)"
            r"\s*[:\s]*\s*" + RE_NUM, text, re.IGNORECASE
        )
        if m:
            return {"type": "SL_MOVE", "new_sl": float(m.group(1))}
        return None

    def _parse_trade(self, text: str) -> dict | None:
        symbol = _extract_symbol(text)
        action = _extract_action(text)
        tps = _extract_tps(text)
        sl = _extract_sl(text)
        zone = _parse_range(text)
        if not symbol or not tps or sl is None:
            return None
        if zone:
            zone_low, zone_high = zone
        else:
            return None
        if zone_low == zone_high:
            zone_high = zone_low + 0.5
            zone_low = zone_low - 0.5
        zone_mid = round((zone_low + zone_high) / 2, 2)
        if not action:
            action = _detect_action_from_tps(zone_low, zone_high, tps)
        if not self._validate_sl(action, zone_mid, sl):
            log.warning(f"SL invalide: {action} entry={zone_mid} SL={sl}")
            return None
        return {
            "type": "TRADE", "symbol": symbol, "action": action,
            "zone_low": zone_low, "zone_mid": zone_mid, "zone_high": zone_high,
            "tps": tps, "tp1": tps[0], "tp_final": tps[-1], "sl": sl,
        }

    @staticmethod
    def _validate_sl(action: str, entry_price: float, sl: float) -> bool:
        if action == "BUY" and sl >= entry_price:
            return False
        if action == "SELL" and sl <= entry_price:
            return False
        return True


# =============================================================
# MT5 BRIDGE (v4.1 — volume min broker + group fix)
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
            login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER,
            path=MT5_PATH if os.path.exists(MT5_PATH) else None
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
            log.warning("Algo Trading désactivé — tentative d'activation...")
            try:
                # Activer AutoTrading programmatiquement
                mt5.terminal_info()  # refresh
                import subprocess, time
                # Envoyer F7 ou passer par les settings MT5
                log.warning(
                    "Activez manuellement 'Algo Trading' (bouton vert) dans MT5"
                )
            except Exception:
                pass
        else:
            log.info("Algo Trading actif ✅")
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

    def _validate_volume(self, sym_info, lot: float) -> float:
        """Vérifie et ajuste le volume selon les contraintes du broker."""
        vol_min = sym_info.volume_min
        vol_max = sym_info.volume_max
        vol_step = sym_info.volume_step

        if lot < vol_min:
            log.warning(
                f"Lot {lot} < minimum {vol_min} → ajusté à {vol_min}"
            )
            lot = vol_min
        elif lot > vol_max:
            log.warning(
                f"Lot {lot} > maximum {vol_max} → ajusté à {vol_max}"
            )
            lot = vol_max

        # Arrondir au step le plus proche
        if vol_step > 0:
            lot = round(lot / vol_step) * vol_step
            lot = round(lot, 8)

        return lot

    def place_market_order(
        self, signal: dict, lot: float, tp: float
    ) -> int | None:
        log.info(f"[DEBUG] place_market_order ENTRÉE sym={signal['symbol']} action={signal['action']} lot={lot} tp={tp}")
        sym = self._sym(signal["symbol"])
        if not sym:
            log.error(f"[DEBUG] sym=None pour {signal['symbol']}")
            return None

        # v4.2: Validation volume broker
        lot = self._validate_volume(sym, lot)

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
                    "comment": f"TG-market {datetime.now(timezone.utc):%H:%M}",
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

        # v4.2: Validation volume broker
        lot = self._validate_volume(sym, lot)

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
                "comment": f"TG-limit {datetime.now(timezone.utc):%H:%M}",
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
    to_remove = []
    for entry in manager.active:
        if entry["signal"]["symbol"] != symbol:
            continue
        for o in entry.get("orders", []):
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
    tp2 = all_tps[1] if len(all_tps) >= 2 else all_tps[0]  # TP2 pour le market
    tp_final = all_tps[-1]  # TP final pour le limit
    sl = signal["sl"]
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ORDER_EXPIRY_MIN)

    if not DEMO_MODE and check_conflict(signal, bridge, manager):
        return

    sym_info = bridge._sym(symbol)
    if sym_info is None:
        log.error(f"Signal rejeté — symbole introuvable dans MT5: {symbol}")
        return

    current = bridge.current_price(sym_info.name, action)
    if current is None:
        log.error(f"Signal rejeté — prix indisponible pour {sym_info.name} (action={action})")
        return

    avg_entry = (zone_low + zone_high) / 2
    if not SignalParser._validate_sl(action, avg_entry, sl):
        log.error(
            f"Signal rejeté — SL {sl} invalide pour {action} "
            f"(entry={avg_entry})"
        )
        return

    tick = mt5.symbol_info_tick(sym_info.name)
    if tick and not DEMO_MODE:
        spread_points = abs(tick.ask - tick.bid)
        spread_pips = spread_points / sym_info.point
        if spread_pips > MAX_SPREAD_POINTS:
            log.warning(
                f"Signal ignoré — spread trop large: {spread_pips:.0f} pts "
                f"(max={MAX_SPREAD_POINTS}) | {sym_info.name}"
            )
            return

    existing_positions = mt5.positions_get(symbol=sym_info.name)
    bot_positions = [p for p in (existing_positions or []) if p.magic == MAGIC_NUMBER]
    if len(bot_positions) >= MAX_POSITIONS:
        log.warning(
            f"Signal ignoré — max positions atteint ({len(bot_positions)}/{MAX_POSITIONS}) "
            f"| {sym_info.name}"
        )
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

    orders, tickets = [], []

    if in_zone:
        # ─────────────────────────────────────────────
        # CAS 1: Prix dans la zone
        # 1 × MARKET avec TP=TP2
        # 1 × LIMIT entre SL et zone avec TP=TP_final
        # ─────────────────────────────────────────────

        # Lot split : 50% market, 50% limit
        vol_min    = sym_info.volume_min
        lot_market = max(round(LOT_SIZE * 0.5, 2), vol_min)
        lot_limit  = max(round(LOT_SIZE * 0.5, 2), vol_min)
        log.info(f"CAS 1 lots → market={lot_market} limit={lot_limit} (vol_min={vol_min})")

        # 1) MARKET order avec TP=TP2
        log.info(f"CAS 1 → MARKET {action} lot={lot_market} TP={tp2} SL={sl}")
        try:
            t = bridge.place_market_order(signal, lot_market, tp=tp2)
        except Exception as e:
            log.error(f"MARKET EXCEPTION: {e}")
            t = None

        market_entry_price = current
        if t:
            tickets.append({
                "ticket": t,
                "lot": lot_market,
                "role": "market_tp2",
                "entry_price": market_entry_price,
                "tp_index": 1,
                "tp_target": tp2,
                "tp1": tp2,
                "tp2": tp_final,
                "sl_step": 0,
                "trail_active": False,
            })
            log.info(f"  ✓ MARKET #{t} @{market_entry_price} TP={tp2}")
        else:
            log.error("  ✗ MARKET échoué")

        # 2) LIMIT order entre SL et zone, TP=TP_final
        if action == "BUY":
            limit_price = round((sl + zone_low) / 2, sym_info.digits)
        else:
            limit_price = round((zone_high + sl) / 2, sym_info.digits)

        log.info(f"CAS 1 → LIMIT {action} @{limit_price} lot={lot_limit} TP={tp_final} SL={sl}")
        o = bridge.place_limit_order(signal, lot_limit, limit_price, tp_final, expiry)
        if o:
            orders.append({
                "order": o,
                "lot": lot_limit,
                "price": limit_price,
                "role": "limit_catch",
                "tp_index": len(all_tps) - 1,
                "tp_target": tp_final,
                "tp1": tp2,
                "tp2": tp_final,
                "sl_step": 0,
                "trail_active": False,
                "_market_entry_price": market_entry_price,
            })
            log.info(f"  ✓ LIMIT #{o} @{limit_price} TP={tp_final}")
        else:
            log.error(f"  ✗ LIMIT échoué @{limit_price}")

    else:
        # ─────────────────────────────────────────────
        # CAS 2: Prix hors zone
        # 2 × LIMIT: zone_edge + zone_opposite, TP=TP_final pour les 2
        # Le code gère la fermeture/BE/trailing à TP2
        # ─────────────────────────────────────────────

        lot_per_order = max(round(LOT_SIZE / 2, 2), sym_info.volume_min)
        if lot_per_order < sym_info.volume_min:
            lot_per_order = sym_info.volume_min

        if action == "BUY":
            # Prix au-dessus de la zone → limit en dessous
            price_1 = zone_high   # zone edge (plus proche du prix)
            price_2 = zone_low    # zone opposite (plus loin)
        else:
            # Prix en dessous de la zone → limit au-dessus
            price_1 = zone_low    # zone edge (plus proche du prix)
            price_2 = zone_high   # zone opposite (plus loin)

        # Limit 1: zone_edge → TP=TP_final (code gère la sortie à TP2)
        log.info(f"CAS 2 → LIMIT_1 {action} @{price_1} lot={lot_per_order} TP={tp_final} SL={sl}")
        o1 = bridge.place_limit_order(signal, lot_per_order, price_1, tp_final, expiry)
        if o1:
            tp_idx_1 = all_tps.index(tp_final) if tp_final in all_tps else len(all_tps) - 1
            orders.append({
                "order":      o1,
                "lot":        lot_per_order,
                "price":      price_1,
                "role":       "limit_1",
                "tp_index":   tp_idx_1,
                "tp_target":  tp_final,
                "tp1":        tp2,
                "tp2":        tp_final,
                "sl_step":    0,
                "trail_active": False,
            })
            log.info(f"  ✓ LIMIT_1 #{o1} @{price_1} TP={tp_final}")
        else:
            log.error(f"  ✗ LIMIT_1 échoué @{price_1}")

        # Limit 2: zone_opposite → TP=TP_final
        log.info(f"CAS 2 → LIMIT_2 {action} @{price_2} lot={lot_per_order} TP={tp_final} SL={sl}")
        o2 = bridge.place_limit_order(signal, lot_per_order, price_2, tp_final, expiry)
        if o2:
            tp_idx_2 = all_tps.index(tp_final) if tp_final in all_tps else len(all_tps) - 1
            orders.append({
                "order":      o2,
                "lot":        lot_per_order,
                "price":      price_2,
                "role":       "limit_2",
                "tp_index":   tp_idx_2,
                "tp_target":  tp_final,
                "tp1":        tp2,
                "tp2":        tp_final,
                "sl_step":    0,
                "trail_active": False,
            })
            log.info(f"  ✓ LIMIT_2 #{o2} @{price_2} TP={tp_final}")
        else:
            log.error(f"  ✗ LIMIT_2 échoué @{price_2}")

    if not orders and not tickets:
        log.error("Aucun ordre placé.")
        return

    entry = {
        "signal": signal,
        "orders": orders,
        "tickets": tickets,
        "expiry": expiry,
        "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    manager.register(entry)
    tracker.log_trade_open(entry)

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
# TRADE MANAGER (v4.2 — async-safe)
# =============================================================
class TradeManager:

    def __init__(self, bridge: MT5Bridge, reporter: "TradeReporter", tracker=None):
        self.bridge = bridge
        self.reporter = reporter
        self.tracker = tracker
        self.active = []
        self._lock = threading.Lock()  # FIX: thread-safe (to_thread) au lieu de asyncio.Lock
        self._stop = False
        self._task = None

    async def start(self):
        """Démarre la boucle de monitoring en tant que tâche asyncio."""
        self._task = asyncio.create_task(self._loop_async())

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
        if self._task:
            self._task.cancel()

    async def _loop_async(self):
        """Boucle async avec asyncio.to_thread pour les appels MT5 bloquants."""
        while not self._stop:
            await asyncio.sleep(10)
            try:
                await asyncio.to_thread(self._check_all)
            except Exception as exc:
                log.error(f"TradeManager erreur: {exc}")

    def _get_last_pnl(self, ticket: int, symbol: str) -> float:
        """Get P&L for a closed position. Filtre post-requête par symbole exact."""
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        # v4.2: Pas de group=symbol (pattern regex dangereux)
        deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
        if deals:
            # Essai 1: match exact par ticket + symbole
            for deal in reversed(deals):
                if deal.symbol == symbol and (deal.position_id == ticket or deal.order == ticket):
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        return deal.profit
            # Essai 2: match par position_id seul (suffixe symbole différent)
            for deal in reversed(deals):
                if deal.position_id == ticket and deal.entry == mt5.DEAL_ENTRY_OUT:
                    return deal.profit
        return 0.0

    def _get_pos(self, ticket: int):
        r = mt5.positions_get(ticket=ticket)
        return r[0] if r else None

    def _resolve_order(self, order_ticket: int, symbol: str):
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
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
        now = datetime.now(timezone.utc)
        to_remove = []

        with self._lock:
            entries_snapshot = list(self.active)

        for entry in entries_snapshot:
            sig = entry["signal"]
            symbol = sig["symbol"]
            action = sig["action"]

            # Resolve pending limit orders → tickets
            still_pending = []
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

            # ─────────────────────────────────────────
            # CAS 1: TP2 hité → gérer le limit_catch
            # Déclencheur : position market disparue (fermée par MT5 au TP)
            # ─────────────────────────────────────────
            market_tk = None
            for t in entry["tickets"]:
                if t.get("role") == "market_tp2":
                    market_tk = t
                    break

            if market_tk:
                market_entry = market_tk.get("entry_price", 0)
                market_pos   = self._get_pos(market_tk["ticket"])
                market_closed = market_pos is None  # fermé par MT5 (TP atteint)

                if market_closed and not market_tk.get("_cas1_handled"):
                    market_tk["_cas1_handled"] = True

                    # Chercher le limit dans les tickets déjà résolus
                    limit_ticket = None
                    for tk in entry["tickets"]:
                        if tk.get("role") == "limit_catch":
                            limit_ticket = tk
                            break

                    # Chercher le limit encore pending dans orders
                    limit_order = None
                    for o in entry["orders"]:
                        if o.get("role") == "limit_catch":
                            limit_order = o
                            break

                    if limit_ticket:
                        # Scénario 2: Limit exécuté → BE + Trailing
                        pos = self._get_pos(limit_ticket["ticket"])
                        if pos:
                            log.info(
                                f"CAS 1 TP2 hité → Limit #{limit_ticket['ticket']} exécuté "
                                f"@{pos.price_open} → SL vers BE ({market_entry})"
                            )
                            self.bridge.modify_sl(
                                limit_ticket["ticket"],
                                market_entry,
                                label="[BE after TP2]"
                            )
                            limit_ticket["trail_active"] = True
                            limit_ticket["sl_step"] = 1
                            log.info(f"Trail activé #{limit_ticket['ticket']} après TP2")
                        else:
                            log.info(
                                f"CAS 1 TP2 hité → Limit #{limit_ticket['ticket']} déjà fermé"
                            )

                    elif limit_order:
                        # Vérifier si le limit pending vient d'être exécuté
                        pos = self._resolve_order(limit_order["order"], symbol)
                        if pos:
                            log.info(
                                f"CAS 1 TP2 hité → Limit #{limit_order['order']} exécuté "
                                f"@{pos.price_open} → SL vers BE ({market_entry})"
                            )
                            self.bridge.modify_sl(
                                pos.ticket,
                                market_entry,
                                label="[BE after TP2]"
                            )
                            tk = {
                                "ticket":      pos.ticket,
                                "lot":         limit_order["lot"],
                                "role":        "limit_catch",
                                "entry_price": pos.price_open,
                                "tp_index":    limit_order.get("tp_index", 0),
                                "tp_target":   limit_order.get("tp_target", 0),
                                "tp1":         limit_order["tp1"],
                                "tp2":         limit_order["tp2"],
                                "sl_step":     1,
                                "trail_active": True,
                            }
                            entry["tickets"].append(tk)
                            entry["orders"].remove(limit_order)
                            log.info(f"Trail activé #{pos.ticket} après TP2")
                        else:
                            # Scénario 1: Limit PAS exécuté → annuler
                            log.info(
                                f"CAS 1 TP2 hité → Limit #{limit_order['order']} non exécuté → annulation"
                            )
                            self.bridge.cancel_order(limit_order["order"])
                            entry["orders"].remove(limit_order)

            # ─────────────────────────────────────────
            # CAS 2: Prix atteint TP2 → gérer limit_1 et limit_2
            # Les 2 limits ont TP=TP_final, le code gère la sortie à TP2
            # Option A: aucun rempli → annuler les 2
            # Option B: limit_1 remplie → annuler limit_2, trailing sur limit_1
            # Option C: les 2 remplies → fermer limit_1, SL limit_2 → entrée L1, trailing sur L2
            # ─────────────────────────────────────────

            # Récupérer le niveau TP2 depuis l'entrée (tp1 = TP2 du signal)
            cas2_tp2_level = 0
            for t in entry["tickets"]:
                if t.get("tp1"):
                    cas2_tp2_level = t["tp1"]
                    break

            # Déclencheur : prix a atteint TP2
            cas2_tp2_hit = False
            if cas2_tp2_level > 0:
                if action == "BUY" and current >= cas2_tp2_level:
                    cas2_tp2_hit = True
                elif action == "SELL" and current <= cas2_tp2_level:
                    cas2_tp2_hit = True

            if cas2_tp2_hit:
                # Trouver limit_1 dans les tickets (remplie) et les orders (pending)
                cas2_limit1_tk = None
                for tk in entry["tickets"]:
                    if tk.get("role") == "limit_1":
                        cas2_limit1_tk = tk
                        break
                cas2_limit1_order = None
                for o in entry["orders"]:
                    if o.get("role") == "limit_1":
                        cas2_limit1_order = o
                        break

                # Trouver limit_2 dans les tickets (remplie) et les orders (pending)
                limit2_ticket = None
                for tk in entry["tickets"]:
                    if tk.get("role") == "limit_2":
                        limit2_ticket = tk
                        break
                limit2_order = None
                for o in entry["orders"]:
                    if o.get("role") == "limit_2":
                        limit2_order = o
                        break

                # Vérifier si chaque limit a été remplie (a un ticket avec entry_price)
                limit1_was_filled = cas2_limit1_tk is not None and cas2_limit1_tk.get("entry_price", 0) > 0
                limit2_was_filled = limit2_ticket is not None and limit2_ticket.get("entry_price", 0) > 0

                if not limit1_was_filled and not limit2_was_filled:
                    # Option A: aucun ordre rempli → annuler TOUS les ordres pending
                    log.info("CAS 2 Option A → prix a atteint TP2 sans remplir les limits → annuler tout")
                    for o in list(entry["orders"]):
                        if o.get("role") in ("limit_1", "limit_2"):
                            self.bridge.cancel_order(o["order"])
                            entry["orders"].remove(o)

                elif limit1_was_filled and not limit2_was_filled:
                    # Option B: limit_1 remplie, limit_2 jamais remplie
                    # → annuler limit_2 pending + SL de limit_1 → BE + trailing vers TP_final
                    log.info("CAS 2 Option B → limit_1 remplie, prix a atteint TP2")
                    if limit2_order:
                        log.info("  → annuler limit_2 pending")
                        self.bridge.cancel_order(limit2_order["order"])
                        entry["orders"].remove(limit2_order)
                    if cas2_limit1_tk:
                        limit1_entry = cas2_limit1_tk.get("entry_price", 0)
                        pos1 = self._get_pos(cas2_limit1_tk["ticket"])
                        if pos1:
                            log.info(
                                f"  → SL limit_1 → BE ({limit1_entry}) + trailing vers TP_final"
                            )
                            self.bridge.modify_sl(
                                cas2_limit1_tk["ticket"],
                                limit1_entry,
                                label="[BE CAS2]"
                            )
                            cas2_limit1_tk["trail_active"] = True
                            cas2_limit1_tk["sl_step"] = 1

                elif limit1_was_filled and limit2_was_filled:
                    # Option C: les 2 remplies → fermer limit_1 manuellement à TP2
                    # → SL de limit_2 → entrée de limit_1 + trailing vers TP_final
                    limit1_entry = cas2_limit1_tk.get("entry_price", 0) if cas2_limit1_tk else 0
                    log.info(
                        f"CAS 2 Option C → les 2 remplies, prix a atteint TP2 "
                        f"→ fermer limit_1 + SL limit_2 → BE ({limit1_entry}) + trailing vers TP_final"
                    )
                    # Fermer limit_1 manuellement (pas de TP sur limit_1, broker ne ferme pas)
                    if cas2_limit1_tk and self._get_pos(cas2_limit1_tk["ticket"]):
                        self.bridge.close_position(
                            cas2_limit1_tk["ticket"],
                            comment="CAS2-TP2-manual-close"
                        )
                    # SL de limit_2 → entrée de limit_1 + trailing
                    if limit2_ticket:
                        pos2 = self._get_pos(limit2_ticket["ticket"])
                        if pos2 and limit1_entry > 0:
                            self.bridge.modify_sl(
                                limit2_ticket["ticket"],
                                limit1_entry,
                                label="[BE CAS2 after L1 close]"
                            )
                            limit2_ticket["trail_active"] = True
                            limit2_ticket["sl_step"] = 1
                            log.info(f"  → Trail activé sur limit_2 #{limit2_ticket['ticket']} vers TP_final")

            # After first TP hit: move SL to breakeven for positions in profit
            if tp_indices_closed and 0 in tp_indices_closed:
                for t in entry["tickets"]:
                    if self._get_pos(t["ticket"]) and t.get("sl_step", 0) == 0:
                        ep = t.get("entry_price", 0)
                        if action == "BUY" and current > ep:
                            self.bridge.modify_sl(
                                t["ticket"], ep, label="[BE after TP1]"
                            )
                            t["sl_step"] = 1
                            log.info(f"BE activé #{t['ticket']} → SL={ep}")
                        elif action == "SELL" and current < ep:
                            self.bridge.modify_sl(
                                t["ticket"], ep, label="[BE after TP1]"
                            )
                            t["sl_step"] = 1
                            log.info(f"BE activé #{t['ticket']} → SL={ep}")
                        else:
                            log.info(
                                f"BE reporté #{t['ticket']} — "
                                f"prix={current} entry={ep} (pas encore en profit)"
                            )

            # Trailing SL update for active positions
            # (activation is handled by CAS 1/CAS 2 specific code above)
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
                    if pos.sl == 0 or nsl > pos.sl:
                        self.bridge.modify_sl(
                            t["ticket"],
                            round(nsl, d),
                            label="[Trail BUY]",
                        )
                else:
                    nsl = current + gap
                    if pos.sl == 0 or nsl < pos.sl:
                        self.bridge.modify_sl(
                            t["ticket"],
                            round(nsl, d),
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
                if hasattr(self, "tracker") and self.tracker:
                    self.tracker.log_trade_close(entry, total_pnl)
                if _supa_connected and _supa:
                    supa_id = entry.get("_supa_trade_id")
                    if supa_id:
                        result_str = "WIN" if total_pnl > 0 else ("BE" if total_pnl == 0 else "LOSS")
                        open_date = entry.get("_open_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
                        try:
                            # FIX: parser avec timezone pour éviter TypeError naive vs aware
                            open_dt = datetime.strptime(open_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            duree = (datetime.now(timezone.utc) - open_dt).total_seconds() / 60
                        except Exception:
                            duree = 0
                        _supa.log_trade_close(supa_id, result_str, total_pnl, duree)
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)


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
                _rc = int(REPORT_CHANNEL) if REPORT_CHANNEL.lstrip("-").isdigit() else REPORT_CHANNEL
                self._report_entity = await client.get_entity(_rc)
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
            f"📅 {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}\n"
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
            f"📅 {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}\n"
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
            f"📅 {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}\n"
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
            f"📅 {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}\n"
            f"📡 Canal : {canal}\n"
            f"📊 {sig['symbol']} {sig['action']}\n"
            f"📍 Zone : {zone}\n"
            f"❌ SL : {sig['sl']}\n"
            f"💰 P&L TOTAL : {total_pnl:+.2f} $\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await self.send_tg(msg)


# =============================================================
# SHUTDOWN TIMER
# =============================================================
async def shutdown_watcher(reporter, tracker, bridge, manager, news_mgr):
    """Surveille le temps restant et envoie le rapport avant fermeture."""

    if RUNTIME_MINUTES <= 0:
        log.info("[SHUTDOWN] Pas de durée définie → pas de timer")
        return

    end_time = START_TIME + timedelta(minutes=RUNTIME_MINUTES)
    log.info(
        f"[SHUTDOWN] Session de {RUNTIME_MINUTES} min → "
        f"fin prévue à {end_time:%H:%M:%S}"
    )

    while not _shutdown_event.is_set():
        now = datetime.now(timezone.utc)
        remaining = (end_time - now).total_seconds() / 60

        if remaining <= SHUTDOWN_MARGIN_MIN and not _report_event.is_set():
            _report_event.set()
            log.info(
                f"[SHUTDOWN] Fin dans {remaining:.0f} min → "
                f"envoi du rapport final"
            )
            await tracker.send_final_report(reporter)
            log.info("[SHUTDOWN] Rapport envoyé. Le bot continue...")
            break

        if int(remaining) % 5 == 0 and remaining > SHUTDOWN_MARGIN_MIN:
            log.info(f"[SHUTDOWN] Reste {remaining:.0f} min")

        await asyncio.sleep(30)


def sigterm_handler():
    """Appelé quand SIGTERM est reçu (timeout GitHub Actions)."""
    log.info("[SHUTDOWN] SIGTERM reçu → arrêt propre")
    _shutdown_event.set()
    if not _report_event.is_set():
        _report_event.set()
        log.info("[SHUTDOWN] Envoi forcé du rapport...")


# =============================================================
# MAIN
# =============================================================
async def main():
    parser = SignalParser()
    bridge = MT5Bridge()
    reporter = TradeReporter()
    tracker = PerformanceTracker()
    manager = None

    if not bridge.connect():
        log.critical("Bot arrêté — corrigez MT5 puis relancez.")
        return

    # v4.2: TradeManager async
    manager = TradeManager(bridge, reporter, tracker)
    await manager.start()

    news_mgr = NewsManager(bridge)
    news_mgr.set_manager(manager)
    await news_mgr.start()

    client = TelegramClient("session_trading", API_ID, API_HASH)
    await client.start()
    log.info("Telegram connecté.")

    await reporter.set_telegram_client(client)

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, sigterm_handler)
        log.info("[SHUTDOWN] SIGTERM handler installé")
    except NotImplementedError:
        signal.signal(signal.SIGTERM, lambda s, f: sigterm_handler())
        log.info("[SHUTDOWN] SIGTERM handler installé (fallback)")

    chats = []

    channel_names = [
        ("TG_CHANNEL", CHANNEL_NAME),
        ("TG_CHANNEL_2", CHANNEL_NAME_2),
        ("TG_CHANNEL_3", CHANNEL_NAME_3),
        ("TG_CHANNEL_4", CHANNEL_NAME_4),
    ]

    channel_list = [ch for _, ch in channel_names if ch]
    if _supa_connected and _supa:
        _supa.start_session(
            runtime_minutes=RUNTIME_MINUTES,
            channels=channel_list,
            lot_size=LOT_SIZE,
            mode="DEMO" if DEMO_MODE else "LIVE",
        )
    entity_to_name = {}

    for env_name, ch_value in channel_names:
        if not ch_value:
            continue
        try:
            # Convertir en int si c'est un ID numérique
            # ex: "-1001666520346" → int(-1001666520346)
            ch_resolved = int(ch_value) if ch_value.lstrip("-").isdigit() else ch_value
            entity = await client.get_entity(ch_resolved)
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
            # ⚠️ FILTRE HORAIRE DÉSACTIVÉ TEMPORAIREMENT (v4.2-patch)
            # blocked, desc = in_blocked_window()
            # if blocked:
            #     log.info(f"[TIME] Signal ignoré — {desc}")
            #     return
            if NEWS_ENABLED and news_mgr.is_blocked():
                log.info("[NEWS] Signal ignoré — protection news")
                return
            execute_signal(signal_data, bridge, manager, tracker)
            for entry in manager.active:
                if entry["signal"] is signal_data:
                    await reporter.on_order_opened(entry)
                    break

    # Banner
    mode = "🧪 DEMO" if DEMO_MODE else "💰 LIVE"
    log.info("=" * 55)
    log.info(f" TRADINGBOT V4.1 — {mode}")
    log.info(f" Canaux surveillés : {len(chats)}")
    for env_name, ch_value in channel_names:
        if ch_value:
            log.info(f"  {env_name} : {ch_value}")
    if REPORT_CHANNEL:
        log.info(f" Canal de rapport : {REPORT_CHANNEL}")
    log.info(f" Lot : {LOT_SIZE}")
    log.info(f" Trail SL : {TRAIL_POINTS} pts")
    log.info(f" News filter : {'ON' if NEWS_ENABLED else 'OFF'}")
    log.info(f" Time filter : OFF (désactivé temporairement)")
    if RUNTIME_MINUTES > 0:
        end = START_TIME + timedelta(minutes=RUNTIME_MINUTES)
        log.info(f" Session : {RUNTIME_MINUTES} min (fin {end:%H:%M})")
    log.info(f" Performance : Supabase + rapports Telegram")
    log.info("=" * 55)

    try:
        shutdown_task = asyncio.create_task(
            shutdown_watcher(reporter, tracker, bridge, manager, news_mgr)
        )
        await client.run_until_disconnected()
    finally:
        if not _report_event.is_set() and reporter._tg_client:
            _report_event.set()
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
