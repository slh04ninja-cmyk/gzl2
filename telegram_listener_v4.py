"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 4.4.0 — multi-channel + improved parser + no TG reports
=============================================================
 Changements v4.4.0 :
 - NEW: Support 6 canaux Telegram (TG_CHANNEL_1 à TG_CHANNEL_6)
 - NEW: Parser V5.1 — entry sans range, 10 patterns TP, 10 patterns SL
 - DEL: Suppression des rapports Telegram automatiques (TradeReporter)
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
CHANNEL_NAME = os.getenv("TG_CHANNEL_1", os.getenv("TG_CHANNEL", ""))
CHANNEL_NAME_2 = os.getenv("TG_CHANNEL_2", "")
CHANNEL_NAME_3 = os.getenv("TG_CHANNEL_3", "")
CHANNEL_NAME_4 = os.getenv("TG_CHANNEL_4", "")
CHANNEL_NAME_5 = os.getenv("TG_CHANNEL_5", "")
CHANNEL_NAME_6 = os.getenv("TG_CHANNEL_6", "")
CHANNEL_NAME_7 = os.getenv("TG_CHANNEL_7", "")
CHANNEL_NAME_8 = os.getenv("TG_CHANNEL_8", "")
CHANNEL_NAME_9 = os.getenv("TG_CHANNEL_9", "")

# Mapping canal → numéro (pour commentaire MT5)
CHANNEL_NUM_MAP = {}
for _i, _name in enumerate([CHANNEL_NAME, CHANNEL_NAME_2, CHANNEL_NAME_3,
                             CHANNEL_NAME_4, CHANNEL_NAME_5, CHANNEL_NAME_6,
                             CHANNEL_NAME_7, CHANNEL_NAME_8, CHANNEL_NAME_9], 1):
    if _name:
        CHANNEL_NUM_MAP[_name] = _i
        if _name.lstrip("-").isdigit():
            CHANNEL_NUM_MAP[_name.lstrip("-")] = _i
            # Stocker aussi avec le tiret pour lookup direct
            if _name not in CHANNEL_NUM_MAP:
                CHANNEL_NUM_MAP[_name] = _i

MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "")
MT5_PATH     = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")

MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20250226"))
SLIPPAGE = int(os.getenv("SLIPPAGE", "20"))
ORDER_EXPIRY_MIN = int(os.getenv("ORDER_EXPIRY_MINUTES", "240"))
TRAIL_POINTS = float(os.getenv("TRAIL_POINTS", "200"))
LOT_SIZE = float(os.getenv("LOT_TOTAL", "0.01"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "6"))
MAX_SPREAD_POINTS = float(os.getenv("MAX_SPREAD_POINTS", "50"))
TP_TRIGGER = int(os.getenv("TP_TRIGGER", "3"))

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

POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))
PNL_TRIGGER_USD = float(os.getenv("PNL_TRIGGER_USD", "5.0"))

RUNTIME_MINUTES = int(os.getenv("RUNTIME_MINUTES", "0"))
SHUTDOWN_MARGIN_MIN = 5

START_TIME = datetime.now(timezone.utc)
_shutdown_event = asyncio.Event()


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

# Initialiser le tracker
if _tracker_available:
    _tracker = get_tracker(_supa if _supa_connected else None)
    log.info("[TRACK] Module de tracking initialisé")

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

    def print_final_report(self):
        if self._report_sent:
            return
        self._report_sent = True
        log.info("[PERF] Rapport final:")
        summary = self.format_session_summary()
        for line in summary.split("\n"):
            log.info(line)


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


# ------------------------------------------------------------------
# SIGNAL PARSER — importé depuis signal_parser.py (v5.1)
# ------------------------------------------------------------------
from signal_parser import SignalParser, is_spam, TradeSignal, detect_format, FormatProfile

# Tracking module (enrichissement Supabase)
try:
    from tracking import get_tracker
    _tracker = None
    _tracker_available = True
except ImportError:
    _tracker = None
    _tracker_available = False


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
        self, signal: dict, lot: float, tp: float, comment: str = "TG-market"
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
                    "comment": comment,
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
        tp: float, expiry: datetime, comment: str = "TG-limit"
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
                "comment": comment,
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
    tp_final = all_tps[-1]  # TP final pour market + limit

    # Ignorer le trade si TP_TRIGGER > nombre de TPs du signal
    if TP_TRIGGER > len(all_tps):
        log.warning(
            f"Signal ignoré — TP_TRIGGER={TP_TRIGGER} mais signal n'a que {len(all_tps)} TPs "
            f"({signal['symbol']} {signal['action']})"
        )
        return

    tp_trigger_idx = TP_TRIGGER - 1  # index 0-based (TP3 → index 2)
    tp3 = all_tps[tp_trigger_idx]  # TP déclencheur BE/trailing

    # Cas particulier : si tp3 == tp_final, reculer d'un TP
    if tp3 == tp_final and tp_trigger_idx > 0:
        tp_trigger_idx -= 1
        tp3 = all_tps[tp_trigger_idx]
        log.info(f"TP_TRIGGER ajusté : TP{TP_TRIGGER}={tp_final} == tp_final → déclencheur = TP{tp_trigger_idx+1}={tp3}")
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

    # Commentaire MT5 : CHn-Cm (numéro canal + cas)
    ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
    cas_num = 1 if in_zone else 2
    mt5_comment = f"CH{ch_num}-C{cas_num}"

    log.info("=" * 55)
    log.info(f"SIGNAL [{mode}] {action} {symbol} | Canal: {canal} ({mt5_comment})")
    log.info(
        f"Zone [{zone_low} — {zone_mid} — {zone_high}] | Prix={current}"
    )
    log.info(
        f"{'DANS la zone → CAS 1' if in_zone else 'HORS zone → CAS 2'}"
    )
    log.info(f"TPs={all_tps} ({len(all_tps)}) | SL={sl}")
    log.info("=" * 55)

    # ─────────────────────────────────────────────────────
    # SIGNAL À PRIX UNIQUE (pas de zone)
    # Scénario 1: prix entre entry et TP1 → MARKET
    # Scénario 2: prix entre TP1 et TP2 → LIMIT @ entry
    # Scénario 3: sinon → annulé
    # ─────────────────────────────────────────────────────
    is_single_price = signal.get("is_single_price", False)  # détecté par le parser

    if is_single_price and len(all_tps) >= 2:
        entry_price = zone_mid
        tp1 = all_tps[0]
        tp2 = all_tps[1]

        # Scénario 1 : prix entre entry et TP1
        if action == "BUY" and entry_price <= current <= tp1:
            scenario = 1
        elif action == "SELL" and tp1 <= current <= entry_price:
            scenario = 1
        # Scénario 2 : prix entre TP1 et TP2
        elif action == "BUY" and tp1 < current <= tp2:
            scenario = 2
        elif action == "SELL" and tp2 <= current < tp1:
            scenario = 2
        # Scénario 3 : sinon
        else:
            scenario = 3

        if scenario == 3:
            log.info(f"PRIX UNIQUE — Scénario 3 : prix={current} hors zone entry-TP2 → ANNULÉ")
            return

        # Commentaire MT5 : CHn-PU-Sm
        mt5_comment_pu = f"CH{ch_num}-PU-S{scenario}"
        log.info(f"PRIX UNIQUE — Scénario {scenario} | entry={entry_price} TP1={tp1} TP2={tp2} prix={current}")

        if scenario == 1:
            # MARKET @ prix actuel
            log.info(f"  → MARKET {action} @{current} lot={LOT_SIZE} TP={tp_final} SL={sl}")
            try:
                t = bridge.place_market_order(signal, LOT_SIZE, tp=tp_final, comment=mt5_comment_pu)
            except Exception as e:
                log.error(f"  MARKET EXCEPTION: {e}")
                t = None

            if t:
                tickets.append({
                    "ticket": t,
                    "lot": LOT_SIZE,
                    "role": "market_single",
                    "entry_price": current,
                    "tp_index": tp_trigger_idx,
                    "tp_target": tp3,
                    "tp3": tp3,
                    "tp_final": tp_final,
                    "sl_step": 0,
                    "trail_active": False,
                })
                log.info(f"  ✓ MARKET #{t} @{current} TP={tp_final}")
            else:
                log.error("  ✗ MARKET échoué")

        elif scenario == 2:
            # LIMIT @ prix du signal
            log.info(f"  → LIMIT {action} @{entry_price} lot={LOT_SIZE} TP={tp_final} SL={sl}")
            o = bridge.place_limit_order(signal, LOT_SIZE, entry_price, tp_final, expiry, comment=mt5_comment_pu)
            if o:
                orders.append({
                    "order": o,
                    "lot": LOT_SIZE,
                    "price": entry_price,
                    "role": "limit_single",
                    "tp_index": tp_trigger_idx,
                    "tp_target": tp3,
                    "tp3": tp3,
                    "tp_final": tp_final,
                    "sl_step": 0,
                    "trail_active": False,
                })
                log.info(f"  ✓ LIMIT #{o} @{entry_price} TP={tp_final}")
            else:
                log.error(f"  ✗ LIMIT échoué @{entry_price}")

        # Enregistrer et sortir
        if not orders and not tickets:
            log.error("Aucun ordre placé (prix unique).")
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
        return

    # ─────────────────────────────────────────────────────
    # SIGNAL AVEC ZONE (logique existante CAS 1 / CAS 2)
    # ─────────────────────────────────────────────────────
    orders, tickets = [], []

    if in_zone:
        # ─────────────────────────────────────────────
        # CAS 1: Prix dans la zone
        # 1 × MARKET avec TP=TP_final
        # 1 × LIMIT entre SL et zone avec TP=TP_final
        # ─────────────────────────────────────────────

        # Lot split : 50% market, 50% limit
        vol_min    = sym_info.volume_min
        lot_market = max(round(LOT_SIZE * 0.5, 2), vol_min)
        lot_limit  = max(round(LOT_SIZE * 0.5, 2), vol_min)
        log.info(f"CAS 1 lots → market={lot_market} limit={lot_limit} (vol_min={vol_min})")

        # 1) MARKET order avec TP=TP_final
        log.info(f"CAS 1 → MARKET {action} lot={lot_market} TP={tp_final} SL={sl}")
        try:
            t = bridge.place_market_order(signal, lot_market, tp=tp_final, comment=mt5_comment)
        except Exception as e:
            log.error(f"MARKET EXCEPTION: {e}")
            t = None

        market_entry_price = current
        if t:
            tickets.append({
                "ticket": t,
                "lot": lot_market,
                "role": "market_tp3",
                "entry_price": market_entry_price,
                "tp_index": tp_trigger_idx,
                "tp_target": tp3,
                "tp3": tp3,
                "tp_final": tp_final,
                "sl_step": 0,
                "trail_active": False,
            })
            log.info(f"  ✓ MARKET #{t} @{market_entry_price} TP={tp_final} (TP3 trigger={tp3})")
        else:
            log.error("  ✗ MARKET échoué")

        # 2) LIMIT order entre SL et zone, TP=TP_final
        if action == "BUY":
            limit_price = round((sl + zone_low) / 2, sym_info.digits)
        else:
            limit_price = round((zone_high + sl) / 2, sym_info.digits)

        log.info(f"CAS 1 → LIMIT {action} @{limit_price} lot={lot_limit} TP={tp_final} SL={sl}")
        o = bridge.place_limit_order(signal, lot_limit, limit_price, tp_final, expiry, comment=mt5_comment)
        if o:
            orders.append({
                "order": o,
                "lot": lot_limit,
                "price": limit_price,
                "role": "limit_catch",
                "tp_index": len(all_tps) - 1,
                "tp_target": tp_final,
                "tp3": tp3,
                "tp_final": tp_final,
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
        # Si prix entre zone et TP1 → MARKET @ prix actuel
        # Sinon → 2 × LIMIT aux limites de zone
        # ─────────────────────────────────────────────

        tp1 = all_tps[0]

        # Déterminer si le prix est entre la zone et TP1
        if action == "BUY":
            between_zone_tp1 = zone_high < current < tp1
        else:  # SELL
            between_zone_tp1 = tp1 < current < zone_low

        if between_zone_tp1:
            # ── Prix entre zone et TP1 → MARKET + LIMIT à l'autre limite ──
            lot_per_order = max(round(LOT_SIZE / 2, 2), sym_info.volume_min)

            # L'autre limite de zone (plus loin du prix)
            if action == "BUY":
                other_limit = zone_low
            else:
                other_limit = zone_high

            log.info(f"CAS 2 → Prix entre zone et TP1 ({zone_low}-{zone_high} ↔ {tp1}) | prix={current}")

            # 1) MARKET @ prix actuel
            log.info(f"  → MARKET {action} @{current} lot={lot_per_order} TP={tp_final} SL={sl}")
            try:
                t = bridge.place_market_order(signal, lot_per_order, tp=tp_final, comment=mt5_comment)
            except Exception as e:
                log.error(f"  MARKET EXCEPTION: {e}")
                t = None

            if t:
                tickets.append({
                    "ticket": t,
                    "lot": lot_per_order,
                    "role": "market_cas2",
                    "entry_price": current,
                    "tp_index": tp_trigger_idx,
                    "tp_target": tp3,
                    "tp3": tp3,
                    "tp_final": tp_final,
                    "sl_step": 0,
                    "trail_active": False,
                })
                log.info(f"  ✓ MARKET #{t} @{current} TP={tp_final} (TP3 trigger={tp3})")
            else:
                log.error("  ✗ MARKET échoué")

            # 2) LIMIT à l'autre limite de zone
            log.info(f"  → LIMIT {action} @{other_limit} lot={lot_per_order} TP={tp_final} SL={sl}")
            o = bridge.place_limit_order(signal, lot_per_order, other_limit, tp_final, expiry, comment=mt5_comment)
            if o:
                orders.append({
                    "order":      o,
                    "lot":        lot_per_order,
                    "price":      other_limit,
                    "role":       "limit_cas2",
                    "tp_index":   tp_trigger_idx,
                    "tp_target":  tp3,
                    "tp3":        tp3,
                    "tp_final":   tp_final,
                    "sl_step":    0,
                    "trail_active": False,
                })
                log.info(f"  ✓ LIMIT #{o} @{other_limit} TP={tp_final}")
            else:
                log.error(f"  ✗ LIMIT échoué @{other_limit}")

        else:
            # ── Prix loin de la zone → 2 × LIMIT ──
            lot_per_order = max(round(LOT_SIZE / 2, 2), sym_info.volume_min)

            if action == "BUY":
                price_1 = zone_high   # zone edge (plus proche du prix)
                price_2 = zone_low    # zone opposite (plus loin)
            else:
                price_1 = zone_low    # zone edge (plus proche du prix)
                price_2 = zone_high   # zone opposite (plus loin)

            # Limit 1: zone_edge → TP=TP_final
            log.info(f"CAS 2 → LIMIT_1 {action} @{price_1} lot={lot_per_order} TP={tp_final} SL={sl}")
            o1 = bridge.place_limit_order(signal, lot_per_order, price_1, tp_final, expiry, comment=mt5_comment)
            if o1:
                tp_idx_1 = all_tps.index(tp_final) if tp_final in all_tps else len(all_tps) - 1
                orders.append({
                    "order":      o1,
                    "lot":        lot_per_order,
                    "price":      price_1,
                    "role":       "limit_1",
                    "tp_index":   tp_idx_1,
                    "tp_target":  tp_final,
                    "tp3":        tp3,
                    "tp_final":   tp_final,
                    "sl_step":    0,
                    "trail_active": False,
                })
                log.info(f"  ✓ LIMIT_1 #{o1} @{price_1} TP={tp_final}")
            else:
                log.error(f"  ✗ LIMIT_1 échoué @{price_1}")

            # Limit 2: zone_opposite → TP=TP_final
            log.info(f"CAS 2 → LIMIT_2 {action} @{price_2} lot={lot_per_order} TP={tp_final} SL={sl}")
            o2 = bridge.place_limit_order(signal, lot_per_order, price_2, tp_final, expiry, comment=mt5_comment)
            if o2:
                tp_idx_2 = all_tps.index(tp_final) if tp_final in all_tps else len(all_tps) - 1
                orders.append({
                    "order":      o2,
                    "lot":        lot_per_order,
                    "price":      price_2,
                    "role":       "limit_2",
                    "tp_index":   tp_idx_2,
                    "tp_target":  tp_final,
                    "tp3":        tp3,
                    "tp_final":   tp_final,
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

    # Tracking enrichi
    if _tracker and _supa_connected:
        cas_num = 1 if in_zone else 2
        signal_type = "PU" if is_single_price else f"CAS{cas_num}"
        enriched = _tracker.enrich_trade_data(signal, current, LOT_SIZE, sl, tp_final, cas_num)
        if supa_trade_id:
            _tracker.update_trade_tracking(supa_trade_id, enriched)
            _tracker.track_open(supa_trade_id, signal, current, LOT_SIZE, sl, tp_final, signal_type)


# =============================================================
# TRADE MANAGER (v4.2 — async-safe)
# =============================================================
class TradeManager:

    def __init__(self, bridge: MT5Bridge, tracker=None):
        self.bridge = bridge
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

    def _check_pnl_trigger(self, entry: dict) -> bool:
        """Vérifie si une position du trade a atteint le P&L trigger."""
        for t in entry.get("tickets", []):
            if t.get("trail_active") or t.get("_pnl_handled"):
                continue
            pos = self._get_pos(t["ticket"])
            if pos and pos.profit >= PNL_TRIGGER_USD:
                return True
        return False

    async def _loop_async(self):
        """Boucle async avec asyncio.to_thread pour les appels MT5 bloquants."""
        while not self._stop:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            try:
                await asyncio.to_thread(self._check_all)
            except Exception as exc:
                log.error(f"TradeManager erreur: {exc}")

    def _get_last_pnl(self, ticket: int, symbol: str) -> float:
        """Get P&L for a closed position. Filtre post-requête par symbole exact."""
        since = datetime.now(timezone.utc) - timedelta(days=7)
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

    def _get_close_reason(self, ticket: int, symbol: str) -> str:
        """Get close reason for a position. Returns 'TP', 'SL', or 'OTHER'."""
        since = datetime.now(timezone.utc) - timedelta(days=7)
        deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
        if deals:
            for deal in reversed(deals):
                if deal.symbol == symbol and (deal.position_id == ticket or deal.order == ticket):
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        if deal.reason == mt5.DEAL_REASON_TP:
                            return "TP"
                        elif deal.reason == mt5.DEAL_REASON_SL:
                            return "SL"
            for deal in reversed(deals):
                if deal.position_id == ticket and deal.entry == mt5.DEAL_ENTRY_OUT:
                    if deal.reason == mt5.DEAL_REASON_TP:
                        return "TP"
                    elif deal.reason == mt5.DEAL_REASON_SL:
                        return "SL"
        return "OTHER"

    def _get_pos(self, ticket: int):
        r = mt5.positions_get(ticket=ticket)
        return r[0] if r else None

    def _resolve_order(self, order_ticket: int, symbol: str):
        since = datetime.now(timezone.utc) - timedelta(days=7)
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
                        "tp_index": o.get("tp_index", 0),
                        "tp_target": o.get("tp_target", 0),
                        "tp3": o.get("tp3", 0),
                        "tp_final": o.get("tp_final", 0),
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
                    close_reason = self._get_close_reason(t["ticket"], symbol)
                    if close_reason == "TP":
                        if _supa_connected and _supa:
                            supa_id = entry.get("_supa_trade_id")
                            if supa_id:
                                _supa.log_tp_hit(supa_id, f"TP{tp_idx+1}", tp_val, pnl)
                    elif close_reason == "SL":
                        if _supa_connected and _supa:
                            supa_id = entry.get("_supa_trade_id")
                            if supa_id:
                                _supa.log_sl_hit(supa_id, pnl)
                    else:
                        # OTHER (manual close, etc.) — log as TP if positive, SL if negative
                        if _supa_connected and _supa:
                            supa_id = entry.get("_supa_trade_id")
                            if supa_id:
                                if pnl >= 0:
                                    _supa.log_tp_hit(supa_id, f"TP{tp_idx+1}", tp_val, pnl)
                                else:
                                    _supa.log_sl_hit(supa_id, pnl)

            # ─────────────────────────────────────────
            # GESTION UNIFIÉE TP3 — BE + trailing sur position restante
            # ─────────────────────────────────────────

            # === PRIX UNIQUE (market_single / limit_single) ===
            pu_tp3_level = 0
            pu_market_tk = None
            pu_limit_tk = None
            pu_limit_order = None
            for t in entry["tickets"]:
                if t.get("role") == "market_single":
                    pu_market_tk = t
                    if t.get("tp3"): pu_tp3_level = t["tp3"]
                elif t.get("role") == "limit_single":
                    pu_limit_tk = t
                    if t.get("tp3") and pu_tp3_level == 0: pu_tp3_level = t["tp3"]
            for o in entry["orders"]:
                if o.get("role") == "limit_single":
                    pu_limit_order = o
                    if o.get("tp3") and pu_tp3_level == 0: pu_tp3_level = o["tp3"]

            # Vérifier si P&L trigger atteint
            pu_pnl_hit = self._check_pnl_trigger(entry)

            if pu_pnl_hit and not entry.get("_pu_handled"):
                entry["_pu_handled"] = True
                log.info("PRIX UNIQUE TP3 atteint → BE + trailing")
                # S1 : market fermé → chercher limit
                if pu_market_tk and self._get_pos(pu_market_tk["ticket"]) is None:
                    if pu_limit_tk:
                        lpos = self._get_pos(pu_limit_tk["ticket"])
                        if lpos:
                            log.info(f"PU → limit exécutée, BE @{pu_market_tk['entry_price']} + trail")
                            self.bridge.modify_sl(pu_limit_tk["ticket"], pu_market_tk["entry_price"], "[PU TP3 BE]")
                            pu_limit_tk["trail_active"] = True
                            pu_limit_tk["sl_step"] = 1
                            pu_limit_tk["trail_last_price"] = current
                    elif pu_limit_order:
                        lpos = self._resolve_order(pu_limit_order["order"], symbol)
                        if lpos:
                            log.info(f"PU → limit tardive, BE @{pu_market_tk['entry_price']} + trail")
                            self.bridge.modify_sl(lpos.ticket, pu_market_tk["entry_price"], "[PU TP3 BE]")
                            tk = {"ticket": lpos.ticket, "lot": pu_limit_order["lot"], "role": "limit_single",
                                  "entry_price": lpos.price_open, "tp_index": pu_limit_order.get("tp_index",0),
                                  "tp_target": pu_limit_order.get("tp_target",0), "tp3": pu_limit_order["tp3"],
                                  "tp_final": pu_limit_order["tp_final"], "sl_step": 1, "trail_active": True,
                                  "trail_last_price": current}
                            entry["tickets"].append(tk)
                            entry["orders"].remove(pu_limit_order)
                        else:
                            self.bridge.cancel_order(pu_limit_order["order"])
                            entry["orders"].remove(pu_limit_order)
                # S2 : limit seule, pas de market
                elif pu_limit_tk and not pu_market_tk:
                    lpos = self._get_pos(pu_limit_tk["ticket"])
                    if lpos and not pu_limit_tk.get("trail_active"):
                        # BE d'abord
                        self.bridge.modify_sl(pu_limit_tk["ticket"], pu_limit_tk.get("entry_price", 0), "[PU S2 BE]")
                        pu_limit_tk["trail_active"] = True
                        pu_limit_tk["sl_step"] = 1
                        pu_limit_tk["trail_last_price"] = current
                        log.info("PU S2 → limit exécutée, BE + trail activé")

            # === CAS 1 : market_tp3 + limit_catch ===
            market_tk = None
            for t in entry["tickets"]:
                if t.get("role") == "market_tp3":
                    market_tk = t
                    break

            if market_tk and not market_tk.get("_cas1_handled"):
                # Vérifier si P&L trigger atteint
                if self._check_pnl_trigger(entry):
                    market_tk["_cas1_handled"] = True
                    market_entry = market_tk.get("entry_price", 0)
                    log.info(f"CAS 1 P&L trigger atteint ({PNL_TRIGGER_USD}$) → prix={current}")
                    
                    limit_ticket = None
                    for tk in entry["tickets"]:
                        if tk.get("role") == "limit_catch":
                            limit_ticket = tk
                            break
                    limit_order = None
                    for o in entry["orders"]:
                        if o.get("role") == "limit_catch":
                            limit_order = o
                            break

                    if limit_ticket:
                        # CAS 2-b : LIMIT remplie → fermer MARKET, BE sur LIMIT
                        pos = self._get_pos(limit_ticket["ticket"])
                        if pos:
                            # Fermer le MARKET
                            market_pos = self._get_pos(market_tk["ticket"])
                            if market_pos:
                                self.bridge.close_position(market_tk["ticket"], "CAS1-TP3-close-market")
                                log.info(f"  MARKET #{market_tk['ticket']} fermé @ TP3")
                            log.info(f"CAS 1 → 2-b limit remplie → BE @{market_entry} + trail")
                            self.bridge.modify_sl(limit_ticket["ticket"], market_entry, "[CAS1 TP3 BE]")
                            limit_ticket["trail_active"] = True
                            limit_ticket["sl_step"] = 1
                            limit_ticket["trail_last_price"] = current
                    elif limit_order:
                        # CAS 2-a : LIMIT pending → annuler LIMIT, MARKET continue avec BE + trailing
                        pos = self._resolve_order(limit_order["order"], symbol)
                        if pos:
                            # LIMIT tardive remplie → fermer MARKET, BE sur LIMIT
                            market_pos = self._get_pos(market_tk["ticket"])
                            if market_pos:
                                self.bridge.close_position(market_tk["ticket"], "CAS1-TP3-close-market")
                                log.info(f"  MARKET #{market_tk['ticket']} fermé @ TP3")
                            log.info(f"CAS 1 → 2-b limit tardive → BE @{market_entry} + trail")
                            self.bridge.modify_sl(pos.ticket, market_entry, "[CAS1 TP3 BE]")
                            tk = {"ticket": pos.ticket, "lot": limit_order["lot"], "role": "limit_catch",
                                  "entry_price": pos.price_open, "tp_index": limit_order.get("tp_index",0),
                                  "tp_target": limit_order.get("tp_target",0), "tp3": limit_order["tp3"],
                                  "tp_final": limit_order["tp_final"], "sl_step": 1, "trail_active": True,
                                  "trail_last_price": current}
                            entry["tickets"].append(tk)
                            entry["orders"].remove(limit_order)
                        else:
                            # LIMIT non remplie → annuler LIMIT, MARKET continue avec BE + trailing
                            log.info(f"CAS 1 → 2-a limit non remplie → annulation #{limit_order['order']}")
                            self.bridge.cancel_order(limit_order["order"])
                            entry["orders"].remove(limit_order)
                            # MARKET continue avec BE + trailing
                            self.bridge.modify_sl(market_tk["ticket"], market_entry, "[CAS1 BE]")
                            market_tk["trail_active"] = True
                            market_tk["sl_step"] = 1
                            market_tk["trail_last_price"] = current
                            log.info(f"  MARKET #{market_tk['ticket']} continue BE @{market_entry} + trailing")
                    else:
                        # Pas de LIMIT → MARKET seul, BE + trailing
                        self.bridge.modify_sl(market_tk["ticket"], market_entry, "[CAS1 BE]")
                        market_tk["trail_active"] = True
                        market_tk["sl_step"] = 1
                        market_tk["trail_last_price"] = current
                        log.info(f"  MARKET #{market_tk['ticket']} continue BE @{market_entry} + trailing")

            # === CAS 2-a : market_cas2 + limit_cas2 (prix entre zone et TP1) ===
            if not entry.get("_cas2a_handled"):
                mc2_tk = None
                lc2_tk = None
                lc2_order = None
                for t in entry["tickets"]:
                    if t.get("role") == "market_cas2":
                        mc2_tk = t
                    elif t.get("role") == "limit_cas2":
                        lc2_tk = t
                for o in entry["orders"]:
                    if o.get("role") == "limit_cas2":
                        lc2_order = o

                if mc2_tk:
                    # Vérifier si P&L trigger atteint
                    if self._check_pnl_trigger(entry):
                        entry["_cas2a_handled"] = True
                        market_entry_c2 = mc2_tk.get("entry_price", 0)
                        log.info(f"CAS 2-a P&L trigger atteint ({PNL_TRIGGER_USD}$) → prix={current}")
                        
                        if lc2_tk:
                            # CAS 3-a-2 : LIMIT remplie → fermer MARKET, BE sur LIMIT
                            lpos = self._get_pos(lc2_tk["ticket"])
                            if lpos:
                                # Fermer le MARKET
                                mc2_pos = self._get_pos(mc2_tk["ticket"])
                                if mc2_pos:
                                    self.bridge.close_position(mc2_tk["ticket"], "C2a-TP3-close-market")
                                    log.info(f"  MARKET #{mc2_tk['ticket']} fermé @ TP3")
                                log.info(f"CAS 2-a → 3-a-2 limit remplie → BE @{market_entry_c2} + trail")
                                self.bridge.modify_sl(lc2_tk["ticket"], market_entry_c2, "[C2a TP3 BE]")
                                lc2_tk["trail_active"] = True
                                lc2_tk["sl_step"] = 1
                                lc2_tk["trail_last_price"] = current
                        elif lc2_order:
                            # Vérifier si LIMIT tardive remplie
                            lpos = self._resolve_order(lc2_order["order"], symbol)
                            if lpos:
                                # LIMIT tardive remplie → fermer MARKET, BE sur LIMIT
                                mc2_pos = self._get_pos(mc2_tk["ticket"])
                                if mc2_pos:
                                    self.bridge.close_position(mc2_tk["ticket"], "C2a-TP3-close-market")
                                    log.info(f"  MARKET #{mc2_tk['ticket']} fermé @ TP3")
                                log.info(f"CAS 2-a → 3-a-2 limit tardive → BE @{market_entry_c2} + trail")
                                self.bridge.modify_sl(lpos.ticket, market_entry_c2, "[C2a TP3 BE]")
                                tk = {"ticket": lpos.ticket, "lot": lc2_order["lot"], "role": "limit_cas2",
                                      "entry_price": lpos.price_open, "tp_index": lc2_order.get("tp_index",0),
                                      "tp_target": lc2_order.get("tp_target",0), "tp3": lc2_order["tp3"],
                                      "tp_final": lc2_order["tp_final"], "sl_step": 1, "trail_active": True,
                                      "trail_last_price": current}
                                entry["tickets"].append(tk)
                                entry["orders"].remove(lc2_order)
                            else:
                                # LIMIT non remplie → annuler LIMIT, MARKET continue avec BE + trailing
                                log.info(f"CAS 2-a → 3-a-1 limit non remplie → annulation #{lc2_order['order']}")
                                self.bridge.cancel_order(lc2_order["order"])
                                entry["orders"].remove(lc2_order)
                                # MARKET continue avec BE + trailing
                                self.bridge.modify_sl(mc2_tk["ticket"], market_entry_c2, "[C2a BE]")
                                mc2_tk["trail_active"] = True
                                mc2_tk["sl_step"] = 1
                                mc2_tk["trail_last_price"] = current
                                log.info(f"  MARKET #{mc2_tk['ticket']} continue BE @{market_entry_c2} + trailing")
                        else:
                            # Pas de LIMIT → MARKET seul, BE + trailing
                            self.bridge.modify_sl(mc2_tk["ticket"], market_entry_c2, "[C2a BE]")
                            mc2_tk["trail_active"] = True
                            mc2_tk["sl_step"] = 1
                            mc2_tk["trail_last_price"] = current
                            log.info(f"  MARKET #{mc2_tk['ticket']} continue BE @{market_entry_c2} + trailing")

            # === CAS 2-b : limit_1 + limit_2 (prix loin de la zone) ===
            if not entry.get("_cas2_handled"):
                # Vérifier si P&L trigger atteint
                cas2_pnl_hit = self._check_pnl_trigger(entry)

                if cas2_pnl_hit:
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

                    l1_filled = cas2_limit1_tk is not None and cas2_limit1_tk.get("entry_price", 0) > 0
                    l2_filled = limit2_ticket is not None and limit2_ticket.get("entry_price", 0) > 0

                    if not l1_filled and not l2_filled:
                        log.info("CAS 2-b TP3 → 3-b-1 aucun rempli → annulation des 2 limits")
                        for o in list(entry["orders"]):
                            if o.get("role") in ("limit_1", "limit_2"):
                                self.bridge.cancel_order(o["order"])
                                entry["orders"].remove(o)
                        entry["_cas2_handled"] = True

                    elif l1_filled and not l2_filled:
                        log.info("CAS 2-b TP3 → 3-b-2 limit_1 remplie → BE @ entry L1 + trail")
                        if limit2_order:
                            self.bridge.cancel_order(limit2_order["order"])
                            entry["orders"].remove(limit2_order)
                        if cas2_limit1_tk:
                            l1_entry = cas2_limit1_tk.get("entry_price", 0)
                            pos1 = self._get_pos(cas2_limit1_tk["ticket"])
                            if pos1:
                                self.bridge.modify_sl(cas2_limit1_tk["ticket"], l1_entry, "[C2b TP3 BE]")
                                cas2_limit1_tk["trail_active"] = True
                                cas2_limit1_tk["sl_step"] = 1
                                cas2_limit1_tk["trail_last_price"] = current
                        entry["_cas2_handled"] = True

                    elif l1_filled and l2_filled:
                        l1_entry = cas2_limit1_tk.get("entry_price", 0) if cas2_limit1_tk else 0
                        log.info(f"CAS 2-b TP3 → 3-b-3 les 2 remplies → fermer L1, BE @{l1_entry} + trail L2")
                        if cas2_limit1_tk and self._get_pos(cas2_limit1_tk["ticket"]):
                            self.bridge.close_position(cas2_limit1_tk["ticket"], "C2b-TP3-close-L1")
                        if limit2_ticket:
                            pos2 = self._get_pos(limit2_ticket["ticket"])
                            if pos2 and l1_entry > 0:
                                self.bridge.modify_sl(limit2_ticket["ticket"], l1_entry, "[C2b TP3 BE]")
                                limit2_ticket["trail_active"] = True
                                limit2_ticket["sl_step"] = 1
                                limit2_ticket["trail_last_price"] = current
                        entry["_cas2_handled"] = True
            # Trailing SL update for active positions
            # Ratio 1:2 — SL bouge de 2$ pour chaque 4$ de mouvement de prix
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
                trail_step = TRAIL_POINTS * pv       # 2$ (200 pts)
                trigger_step = trail_step * 2         # 4$ (400 pts)
                last_price = t.get("trail_last_price", 0)

                if action == "BUY":
                    price_moved = current - last_price
                    if price_moved >= trigger_step:
                        nsl = pos.sl + trail_step if pos.sl > 0 else current - trail_step
                        self.bridge.modify_sl(
                            t["ticket"],
                            round(nsl, d),
                            label="[Trail BUY]",
                        )
                        t["trail_last_price"] = current
                else:
                    price_moved = last_price - current
                    if price_moved >= trigger_step:
                        nsl = pos.sl - trail_step if pos.sl > 0 else current + trail_step
                        self.bridge.modify_sl(
                            t["ticket"],
                            round(nsl, d),
                            label="[Trail SELL]",
                        )
                        t["trail_last_price"] = current

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
                        
                        # Tracking enrichi
                        if _tracker:
                            tracking_data = _tracker.track_close(supa_id, total_pnl, result_str)
                            if tracking_data:
                                _tracker.update_trade_tracking(supa_id, {
                                    "r_multiple": tracking_data["r_multiple"],
                                    "max_drawdown": tracking_data["max_drawdown"],
                                    "signal_type": tracking_data["signal_type"],
                                })
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)


# =============================================================
# MAIN
# =============================================================
async def main():
    parser = SignalParser()
    bridge = MT5Bridge()
    tracker = PerformanceTracker()
    manager = None

    if not bridge.connect():
        log.critical("Bot arrêté — corrigez MT5 puis relancez.")
        return

    manager = TradeManager(bridge, tracker)
    await manager.start()

    news_mgr = NewsManager(bridge)
    news_mgr.set_manager(manager)
    await news_mgr.start()

    client = TelegramClient("session_trading", API_ID, API_HASH)
    await client.start()
    log.info("Telegram connecté.")

    chats = []

    channel_names = [
        ("TG_CHANNEL_1", CHANNEL_NAME),
        ("TG_CHANNEL_2", CHANNEL_NAME_2),
        ("TG_CHANNEL_3", CHANNEL_NAME_3),
        ("TG_CHANNEL_4", CHANNEL_NAME_4),
        ("TG_CHANNEL_5", CHANNEL_NAME_5),
        ("TG_CHANNEL_6", CHANNEL_NAME_6),
        ("TG_CHANNEL_7", CHANNEL_NAME_7),
        ("TG_CHANNEL_8", CHANNEL_NAME_8),
        ("TG_CHANNEL_9", CHANNEL_NAME_9),
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
            ch_resolved = int(ch_value) if ch_value.lstrip("-").isdigit() else ch_value
            entity = await client.get_entity(ch_resolved)
            title = getattr(entity, "title", ch_value)
            chats.append(entity)
            entity_to_name[entity.id] = title
            # Ajouter le titre dans CHANNEL_NUM_MAP pour le lookup CHn-Cm
            ch_num = int(env_name.replace("TG_CHANNEL_", ""))
            CHANNEL_NUM_MAP[title] = ch_num
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

        signal_data._source_channel = canal_name

        if signal_data.signal_type == "CLOSE":
            bridge.close_all(symbol=signal_data.close_symbol)
            return

        elif signal_data.signal_type == "SL_MOVE":
            log.info(
                f"SL MOVE reçu → nouveau SL={signal_data.new_sl}"
            )
            bridge.update_sl_all(signal_data.new_sl)
            return

        elif signal_data.signal_type == "TRADE":
            if NEWS_ENABLED and news_mgr.is_blocked():
                log.info("[NEWS] Signal ignoré — protection news")
                return
            execute_signal(signal_data.to_dict(), bridge, manager, tracker)

    # Banner
    mode = "🧪 DEMO" if DEMO_MODE else "💰 LIVE"
    log.info("=" * 55)
    log.info(f" TRADINGBOT V4.5 — {mode}")
    log.info(f" Canaux surveillés : {len(chats)}")
    for env_name, ch_value in channel_names:
        if ch_value:
            log.info(f"  {env_name} : {ch_value}")
    log.info(f" Lot : {LOT_SIZE}")
    log.info(f" Trail SL : {TRAIL_POINTS} pts")
    log.info(f" Poll interval : {POLL_INTERVAL_SEC}s | P&L trigger : {PNL_TRIGGER_USD}$")
    log.info(f" News filter : {'ON' if NEWS_ENABLED else 'OFF'}")
    log.info(f" Time filter : OFF (désactivé temporairement)")
    if RUNTIME_MINUTES > 0:
        end = START_TIME + timedelta(minutes=RUNTIME_MINUTES)
        log.info(f" Session : {RUNTIME_MINUTES} min (fin {end:%H:%M})")
    log.info(f" Performance : Supabase")
    log.info("=" * 55)

    try:
        await client.run_until_disconnected()
    finally:
        if _supa_connected and _supa:
            total_t = len(tracker._trades_cache)
            total_p = sum(t.get("pnl", 0) for t in tracker._trades_cache)
            _supa.end_session(total_t, total_p)
        if manager:
            manager.stop()
        if news_mgr:
            news_mgr.stop()
        bridge.disconnect()
        tracker.print_final_report()
        log.info("[SHUTDOWN] Bot arrêté proprement.")


if __name__ == "__main__":
    asyncio.run(main())
