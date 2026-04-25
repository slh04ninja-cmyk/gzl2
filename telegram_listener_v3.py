"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 3.0 — Corrigé & Restructuré
=============================================================
 Corrections v3.0 :
 - __init__ au lieu de init (toutes les classes)
 - __name__ au lieu de name
 - asyncio.ensure_future → run_coroutine_threadsafe
 - Thread safety avec Lock sur TradeManager
 - Validation floats avec try/except
 - Return après CLOSE/SL_MOVE dans le handler
 - NewsManager : déblocage stable avec break
 - Détection filling mode automatique
 - Lot configurable via .env
"""

import asyncio
import re
import logging
import time
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
import threading

from telethon import TelegramClient, events
import MetaTrader5 as mt5

load_dotenv()

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
    "100% accurate", "consistency",
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
# PARTIE 2 : NewsManager
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

            # Après la news → débloquer
            if -NEWS_AFTER_MIN <= diff_minutes < 0 and self._blocked:
                remaining = NEWS_AFTER_MIN + diff_minutes
                if remaining <= 0:
                    self._blocked = False
                    log.info(
                        f"[NEWS] {news.get('title', '?')} terminé → reprise"
                    )
                    break  # ← FIX: éviter re-blocage dans la même itération

            # Fermeture proche
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

            # Blocage préventif
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
# PARTIE 3 : TradeReporter
# =============================================================
class TradeReporter:

    def __init__(self):
        self._tg_client = None
        self._report_entity = None
        self._loop = None  # event loop reference

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
# PARTIE 4 : SignalParser
# =============================================================
class SignalParser:

    SYMBOL_MAP = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "OIL": "USOIL"}

    RE_MAIN = re.compile(
        r"(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL)\s+"
        r"(?:INSTANT\s+|NOW[:\s]*)?(?:INSTANT\s+)?(BUY|SELL)"
        r"[^\d\n]{0,30}?(?:Entry:\s*)?\(?\s*([\d.]+)\s*[-/\s]\s*([\d.]+)?\s*\)?",
        re.IGNORECASE,
    )

    RE_MAIN_ALT = re.compile(
        r"(BUY|SELL)\s+TRADE\s+(XAUUSD|GOLD|XAU/USD)", re.IGNORECASE
    )

    RE_ZONE_LINE = re.compile(r"^\s*([\d.]+)\s*[-/]\s*([\d.]+)\s*$", re.MULTILINE)

    RE_TARGET_OPEN = re.compile(r"Target\s*:\s*open", re.IGNORECASE)

    RE_SL_TARGET = re.compile(
        r"(?:📍\s*)?Stop\s+Loss\s*:\s*([\d.]+)", re.IGNORECASE
    )

    RE_TP = re.compile(
        r"(?:\U0001F3AF|\U0001F4CA|\u27A4|\u25BA|\u25B6)?\s*"
        r"TP[\u00b9\u00b2\u00b3\u2074\u2075\d]*[-.:]\s*"
        r"([\d]{3,}(?:\.\d+)?)(?:-max[\d.]+)?",
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
        r"STOP\s+LOSS\s*[:.]\s*\(?\s*([\d]{3,}(?:\.\d+)?)\s*\)?",
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

    RE_CLOSE = re.compile(r"close\s+(all|[A-Z]{3,10})", re.IGNORECASE)

    RE_DAILY_ACTION = re.compile(
        r"(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL)\s+DAILY\s+SIGNAL",
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

    def _build_result(
        self, symbol, action, zone_low, zone_mid, zone_high, tps, sl
    ):
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

        # CLOSE
        close_m = self.RE_CLOSE.search(text.upper())
        if close_m:
            target = close_m.group(1).upper()
            return {
                "type": "CLOSE",
                "symbol": None if target == "ALL" else target,
                "close_all": target == "ALL",
            }

        # SL MOVE
        sl_move_m = self.RE_SL_MOVE.search(text)
        if sl_move_m:
            return {
                "type": "SL_MOVE",
                "new_sl": float(sl_move_m.group(1)),
            }

        # DAILY SIGNAL
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

        # TAKE PROFITE
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

        # SPAM
        if is_spam(text):
            log.debug(f"[SPAM] {text[:60].replace(chr(10), ' ')}")
            return None

        # MAIN
        main_m = self.RE_MAIN.search(text)
        if not main_m:
            return self._parse_alt(text)
        return self._parse_main(main_m, text)

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
                r"(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL)",
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

    def _extract_tps(self, text, action, zone_low, zone_high):
        if self.RE_TARGET_OPEN.search(text):
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

        tps = []
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

        if not tps or sl is None:
            log.warning(f"Incomplet TPs={tps} SL={sl} | {text[:80]}")
            return None

        log.info(
            f"Parsé → {act} {sym} zone [{zl}—{zm}—{zh}] "
            f"TPfinal={tps[-1]} SL={sl} ({len(tps)} TPs)"
        )
        return self._build_result(sym, act, zl, zm, zh, tps, sl)


# =============================================================
# PARTIE 5 : MT5 Bridge
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
            log.warning(
                "Vérifiez que 'Algo Trading' est VERT dans MT5"
            )
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
            for sfx in ["m", "m+", ".a", "pro", "+", ".", "z", "micro", "#", ""]:
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
                    log.info(
                        f"Symbole trouvé par recherche : {info.name}"
                    )
        if info is None:
            log.error(f"Symbole introuvable : {symbol}")
            return None
        self._sym_cache[symbol] = info.name
        if not info.visible:
            mt5.symbol_select(info.name, True)
            time.sleep(0.5)
        return mt5.symbol_info(info.name)

    def _get_filling(self, sym_info) -> int:
        """Détection automatique du mode de remplissage."""
        filling = sym_info.trade_fill_mode
        if filling & mt5.SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        if filling & mt5.SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

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
        sym = self._sym(signal["symbol"])
        if not sym:
            return None
        action = signal["action"]
        tick = mt5.symbol_info_tick(sym.name)
        if not tick:
            return None
        price = tick.ask if action == "BUY" else tick.bid
        otype = (
            mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        )
        filling = self._get_filling(sym)
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
                "type_filling": filling,
            }
        )
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                f"MARKET {action} {sym.name} lot={lot} @{price} "
                f"ticket#{result.order}"
            )
            return result.order
        log.error(
            f"Market échoué | retcode="
            f"{result.retcode if result else 'N/A'}"
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
            f"retcode={result.retcode if result else 'N/A'}"
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
        cprice = (
            tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        )
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

    def modify_sl(self, ticket: int, new_sl: float, label: str = "") -> bool:
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
        log.info(f"SL MOVE appliqué sur {updated} pos/ordres → SL={new_sl}")

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
# PARTIE 6 : Conflit, Exécution, TradeManager
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


def execute_signal(signal: dict, bridge: MT5Bridge, manager):
    action = signal["action"]
    symbol = signal["symbol"]
    zone_low = signal["zone_low"]
    zone_mid = signal["zone_mid"]
    zone_high = signal["zone_high"]

    all_tps = signal["tps"]
    tp1 = all_tps[2] if len(all_tps) >= 3 else all_tps[-1]
    tp2 = all_tps[-1]
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
    log.info(
        f"SIGNAL [{mode}] {action} {symbol} | Canal: {canal}"
    )
    log.info(
        f"Zone [{zone_low} — {zone_mid} — {zone_high}] | Prix={current}"
    )
    log.info(
        f"{'DANS la zone → CAS 1' if in_zone else 'HORS zone → CAS 2'}"
    )
    log.info(f"TP1={tp1} (seuil BE) | TP2={tp2} (trail) | SL={sl}")
    log.info("=" * 55)

    orders, tickets = [], []

    if in_zone:
        # CAS 1
        t1 = bridge.place_market_order(signal, LOT_SIZE, tp=tp2)
        if t1:
            tickets.append(
                {
                    "ticket": t1,
                    "lot": LOT_SIZE,
                    "role": "market",
                    "entry_price": current,
                    "signal_tp1": signal["tp1"],
                    "tp1": tp1,
                    "tp2": tp2,
                    "sl_step": 0,
                    "trail_active": False,
                }
            )
        limit_price = zone_high if action == "SELL" else zone_low
        o1 = bridge.place_limit_order(
            signal, LOT_SIZE, limit_price, tp2, expiry
        )
        if o1:
            orders.append(
                {
                    "order": o1,
                    "lot": LOT_SIZE,
                    "price": limit_price,
                    "role": "limit_cas1",
                    "signal_tp1": signal["tp1"],
                    "tp1": tp1,
                    "tp2": tp2,
                    "sl_step": 0,
                    "trail_active": False,
                }
            )
        log.info(
            f"CAS 1 → MARKET {LOT_SIZE} TP={tp2} | "
            f"LIMIT {LOT_SIZE} @{limit_price}"
        )
    else:
        # CAS 2
        if action == "BUY":
            dist = zone_low - sl
            pa = round(zone_low - dist / 3, sym_info.digits)
            odef = [
                {"price": zone_high, "role": "limit_high"},
                {"price": pa, "role": "limit_low"},
            ]
        else:
            dist = sl - zone_high
            pa = round(zone_high + dist / 3, sym_info.digits)
            odef = [
                {"price": zone_low, "role": "limit_low"},
                {"price": pa, "role": "limit_high"},
            ]
        for od in odef:
            o = bridge.place_limit_order(
                signal, LOT_SIZE, od["price"], tp2, expiry
            )
            if o:
                orders.append(
                    {
                        "order": o,
                        "lot": LOT_SIZE,
                        "price": od["price"],
                        "role": od["role"],
                        "signal_tp1": signal["tp1"],
                        "tp1": tp1,
                        "tp2": tp2,
                        "sl_step": 0,
                        "trail_active": False,
                    }
                )
        log.info(
            "CAS 2 → "
            + " | ".join(
                f"LIMIT @{od['price']} TP={tp2} ({od['role']})"
                for od in odef
            )
        )

    if not orders and not tickets:
        log.error("Aucun ordre placé.")
        return

    manager.register(
        {
            "signal": signal,
            "orders": orders,
            "tickets": tickets,
            "expiry": expiry,
            "_open_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


# ------------------------------------------------------------------
# TRADE MANAGER
# ------------------------------------------------------------------
class TradeManager:

    def __init__(self, bridge: MT5Bridge, reporter: TradeReporter):
        self.bridge = bridge
        self.reporter = reporter
        self.active = []
        self._lock = threading.Lock()  # ← FIX: thread safety
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
            if deal.order == order_ticket and deal.entry == mt5.DEAL_ENTRY_IN:
                positions = mt5.positions_get(ticket=deal.position_id)
                if positions:
                    return positions[0]
        return None

    def _schedule_report(self, coro):
        """Planifie un rapport async de manière thread-safe."""
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

            # 1. Résoudre ordres limits
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

            # 2. Prix courant
            sym_info = self.bridge._sym(symbol)
            if sym_info is None:
                continue
            tick = mt5.symbol_info_tick(sym_info.name)
            if tick is None:
                continue
            current = tick.bid if action == "BUY" else tick.ask

            # 3. Gestion CAS 1 + CAS 2 + Trail
            for t in entry["tickets"]:
                pos = self._get_pos(t["ticket"])
                if pos is None:
                    continue

                ep = t.get("entry_price") or pos.price_open
                signal_tp1 = t.get("signal_tp1", 0)
                tp1_t = t.get("tp1")
                sl_step = t.get("sl_step", 0)
                role = t.get("role", "")

                # CAS 1 : MARKET — TP1 atteint
                if role == "market" and sl_step == 0 and tp1_t:
                    tp1_ok = (action == "BUY" and current >= tp1_t) or (
                        action == "SELL" and current <= tp1_t
                    )
                    if tp1_ok:
                        lt = next(
                            (
                                tk
                                for tk in entry["tickets"]
                                if tk.get("role") == "limit_cas1"
                            ),
                            None,
                        )
                        if lt is None:
                            self.bridge.modify_sl(
                                t["ticket"], ep, label="[BE CAS1 Sc1]"
                            )
                            t["sl_step"] = 1
                            t["trail_active"] = True
                            lo = next(
                                (
                                    o
                                    for o in entry["orders"]
                                    if o.get("role") == "limit_cas1"
                                ),
                                None,
                            )
                            if lo:
                                self.bridge.cancel_order(lo["order"])
                                entry["orders"] = [
                                    o
                                    for o in entry["orders"]
                                    if o.get("role") != "limit_cas1"
                                ]
                        else:
                            self.bridge.close_position(
                                t["ticket"], comment="CAS1-Sc2"
                            )
                            if self._get_pos(lt["ticket"]):
                                self.bridge.modify_sl(
                                    lt["ticket"],
                                    signal_tp1,
                                    label="[BE CAS1 Sc2]",
                                )
                                lt["sl_step"] = 1
                                lt["trail_active"] = True
                        continue

                # CAS 2 : 1er ordre — TP1 atteint
                tr_role = (
                    "limit_high" if action == "BUY" else "limit_low"
                )
                if role == tr_role and sl_step == 0 and tp1_t:
                    tp1_ok = (action == "BUY" and current >= tp1_t) or (
                        action == "SELL" and current <= tp1_t
                    )
                    if tp1_ok:
                        o_role = (
                            "limit_low"
                            if action == "BUY"
                            else "limit_high"
                        )
                        ot = next(
                            (
                                tk
                                for tk in entry["tickets"]
                                if tk.get("role") == o_role
                            ),
                            None,
                        )
                        if ot is None:
                            self.bridge.modify_sl(
                                t["ticket"], ep, label="[BE CAS2 Sc1]"
                            )
                            t["sl_step"] = 1
                            t["trail_active"] = True
                            oo = next(
                                (
                                    o
                                    for o in entry["orders"]
                                    if o.get("role") == o_role
                                ),
                                None,
                            )
                            if oo:
                                self.bridge.cancel_order(oo["order"])
                                entry["orders"] = [
                                    o
                                    for o in entry["orders"]
                                    if o.get("role") != o_role
                                ]
                        else:
                            self.bridge.close_position(
                                t["ticket"], comment="CAS2-Sc2"
                            )
                            if self._get_pos(ot["ticket"]):
                                self.bridge.modify_sl(
                                    ot["ticket"],
                                    signal_tp1,
                                    label="[BE CAS2 Sc2]",
                                )
                                ot["sl_step"] = 1
                                ot["trail_active"] = True
                        continue

                # Trail actif
                if t.get("trail_active"):
                    pl = self._get_pos(t["ticket"])
                    if pl:
                        sym2 = mt5.symbol_info(pl.symbol)
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
                            if nsl > pl.sl:
                                self.bridge.modify_sl(
                                    t["ticket"],
                                    nsl,
                                    label="[Trail BUY]",
                                )
                        else:
                            nsl = current + gap
                            if nsl < pl.sl or pl.sl == 0:
                                self.bridge.modify_sl(
                                    t["ticket"],
                                    nsl,
                                    label="[Trail SELL]",
                                )

            # 4. Détecter fermetures
            for t in entry.get("tickets", []):
                pos = self._get_pos(t["ticket"])
                if pos is None and not t.get("_reported"):
                    t["_reported"] = True
                    pnl = self._get_last_pnl(t["ticket"], symbol)
                    t["_last_pnl"] = pnl
                    if t.get("sl_step", 0) >= 1:
                        self._schedule_report(
                            self.reporter.on_tp_reached(
                                t["ticket"],
                                sig,
                                "Trail/TP",
                                t.get("tp2", 0),
                                pnl,
                            )
                        )
                    else:
                        self._schedule_report(
                            self.reporter.on_sl_hit(
                                t["ticket"], sig, pnl
                            )
                        )

            # 5. Trade terminé
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
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)


# =============================================================
# PARTIE 7 : Main
# =============================================================
async def main():
    parser = SignalParser()
    bridge = MT5Bridge()
    reporter = TradeReporter()
    manager = None

    if not bridge.connect():
        log.critical("Bot arrêté — corrigez MT5 puis relancez.")
        return

    manager = TradeManager(bridge, reporter)
    news_mgr = NewsManager(bridge)
    news_mgr.set_manager(manager)

    client = TelegramClient("session_trading", API_ID, API_HASH)
    await client.start()
    log.info("Telegram connecté.")

    await reporter.set_telegram_client(client)

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

        signal = parser.parse(text)
        if signal is None:
            return

        signal["source_channel"] = canal_name

        if signal["type"] == "CLOSE":
            bridge.close_all(symbol=signal.get("symbol"))
            return  # ← FIX: éviter de continuer

        elif signal["type"] == "SL_MOVE":
            log.info(
                f"SL MOVE reçu → nouveau SL={signal['new_sl']}"
            )
            bridge.update_sl_all(signal["new_sl"])
            return  # ← FIX: éviter de continuer

        elif signal["type"] == "TRADE":
            blocked, desc = in_blocked_window()
            if blocked:
                log.info(f"[TIME] Signal ignoré — {desc}")
                return
            if NEWS_ENABLED and news_mgr.is_blocked():
                log.info("[NEWS] Signal ignoré — protection news")
                return
            execute_signal(signal, bridge, manager)
            with manager._lock:
                for entry in manager.active:
                    if entry["signal"] is signal:
                        await reporter.on_order_opened(entry)
                        break

    mode = "🧪 DEMO" if DEMO_MODE else "💰 LIVE"
    log.info("=" * 55)
    log.info(f" BOT ACTIF — {mode}")
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
    log.info("=" * 55)

    try:
        await client.run_until_disconnected()
    finally:
        if manager:
            manager.stop()
        if news_mgr:
            news_mgr.stop()
        bridge.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
