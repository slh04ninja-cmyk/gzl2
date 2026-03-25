"""
=============================================================
  TELEGRAM -> MT5  |  Bot Trading
=============================================================
  Formats signal supportés :
    XAUUSD INSTANT BUY / Entry: 5177/5175 / TP1: 5181 / SL : 5168
    GOLD SELL 5230/5233 / TP¹.5225 / SL ..5240
    XAUUSD BUY 5215/5212 / TP 5218 / SL 5208
    XAUUSD SELL 5194/5204 / 🎯TP 5191 / SL 5220
    XAUUSD SELL NOW: (5416 5420) / 📊TAKE PROFIT: 1 (5412) / 👎STOP LOSS: (5429)

  CAS 1 — Prix DANS la zone :
    MARKET 0.01 → TP2  puis BE+Trail escalier
    LIMIT  0.01 → TPfinal

  CAS 2 — Prix HORS zone :
    LIMIT 0.01 @high → TP2
    LIMIT 0.01 @mid  → TP3
    LIMIT 0.01 @low-(dist/3) → TPfinal  (BUY)

  Trail SL en escalier :
    TP2 atteint → SL = BE (entrée)
    TP3 atteint → SL = TP1
    TP4 atteint → SL = TP2
    > TPfinal   → Trail en pips

  News Forex Factory + Fenêtres horaires bloquées
  SL MOVE → update immédiat toutes positions + ordres
=============================================================
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
#  CONFIG
# ------------------------------------------------------------------
API_ID            = int(os.getenv("TG_API_ID", "0"))
API_HASH          = os.getenv("TG_API_HASH", "")
CHANNEL_NAME      = os.getenv("TG_CHANNEL", "")
CHANNEL_NAME_2    = os.getenv("TG_CHANNEL_2", "")

MT5_LOGIN         = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD      = os.getenv("MT5_PASSWORD", "")
MT5_SERVER        = os.getenv("MT5_SERVER", "")

MAGIC_NUMBER      = int(os.getenv("MAGIC_NUMBER", "20250226"))
SLIPPAGE          = int(os.getenv("SLIPPAGE", "20"))
ORDER_EXPIRY_MIN  = int(os.getenv("ORDER_EXPIRY_MINUTES", "240"))
TRAIL_POINTS      = float(os.getenv("TRAIL_POINTS", "15"))

NEWS_ENABLED      = os.getenv("NEWS_FILTER_ENABLED", "true").lower() == "true"
NEWS_BLOCK_MIN    = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK", "15"))
NEWS_CLOSE_MIN    = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE", "5"))
NEWS_AFTER_MIN    = int(os.getenv("NEWS_WINDOW_AFTER", "15"))

TIME_FILTER_ENABLED = os.getenv("TIME_FILTER_ENABLED", "true").lower() == "true"
# Fenêtres bloquées UTC : liste de (hh_start, mm_start, hh_end, mm_end)
BLOCKED_WINDOWS   = [(13, 0, 15, 0), (16, 30, 17, 30)]

# ------------------------------------------------------------------
#  LOGGING
# ------------------------------------------------------------------
class OrderFilter(logging.Filter):
    """Filtre console : cache spam Telegram et cycles TradeManager."""
    HIDE = [
        "[SPAM]",
        "[CYCLE]",
    ]
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

# Console handler avec filtre
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
console_handler.addFilter(OrderFilter())
log.addHandler(console_handler)


# ------------------------------------------------------------------
#  FILTRE MESSAGES NON-TRADING
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
#  GESTION FENÊTRES HORAIRES BLOQUÉES
# ------------------------------------------------------------------
def in_blocked_window() -> tuple[bool, str]:
    """Retourne (True, description) si on est dans une fenêtre bloquée UTC."""
    if not TIME_FILTER_ENABLED:
        return False, ""
    now = datetime.now(timezone.utc)
    now_minutes = now.hour * 60 + now.minute
    for (h1, m1, h2, m2) in BLOCKED_WINDOWS:
        start = h1 * 60 + m1
        end   = h2 * 60 + m2
        if start <= now_minutes < end:
            desc = f"{h1:02d}h{m1:02d}-{h2:02d}h{m2:02d} UTC"
            return True, desc
    return False, ""


# ------------------------------------------------------------------
#  GESTIONNAIRE DE NEWS
# ------------------------------------------------------------------
class NewsManager:

    FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    def __init__(self, bridge):
        self.bridge        = bridge
        self.manager       = None  # sera set après création TradeManager
        self._news         = []
        self._blocked      = False
        self._blocked_until = None
        self._stop         = False
        self._thread       = threading.Thread(target=self._loop, daemon=True)
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
            # Vérifier toutes les 30 secondes pour réactivité
            for _ in range(30):
                if self._stop:
                    break
                time.sleep(60)

    def _fetch_news(self):
        try:
            req = urllib.request.Request(self.FF_URL, headers={"User-Agent": "Mozilla/5.0"})
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
                news_time = datetime.fromisoformat(news["date"].replace("Z", "+00:00"))
            except Exception:
                continue

            diff_minutes = (news_time - now).total_seconds() / 60

            # T+NEWS_AFTER_MIN : débloquer
            if -NEWS_AFTER_MIN <= diff_minutes < 0 and self._blocked:
                remaining = NEWS_AFTER_MIN + diff_minutes
                if remaining <= 0:
                    self._blocked = False
                    log.info(f"[NEWS] {news.get('title','?')} terminé → reprise normale")

            # T-NEWS_CLOSE_MIN : fermer positions + annuler ordres
            if 0 < diff_minutes <= NEWS_CLOSE_MIN:
                if not self._blocked:
                    self._blocked = True
                    log.info(f"[NEWS] {news.get('title','?')} dans {diff_minutes:.0f} min → fermeture positions + annulation ordres")
                    if self.manager:
                        self._close_all()

            # T-NEWS_BLOCK_MIN : bloquer nouveaux signaux
            elif NEWS_CLOSE_MIN < diff_minutes <= NEWS_BLOCK_MIN:
                if not self._blocked:
                    self._blocked = True
                    log.info(f"[NEWS] {news.get('title','?')} dans {diff_minutes:.0f} min → nouveaux signaux bloqués")

    def _close_all(self):
        """Annuler ordres + fermer positions avant news."""
        if self.manager:
            for entry in list(self.manager.active):
                for o in entry.get("orders", []):
                    self.bridge.cancel_order(o["order"])
                entry["orders"] = []
        self.bridge.close_all()

    def stop(self):
        self._stop = True


# ------------------------------------------------------------------
#  PARSER DE SIGNAL
# ------------------------------------------------------------------
class SignalParser:

    SYMBOL_MAP = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "OIL": "USOIL"}

    # XAUUSD BUY 5215/5212  ou  XAUUSD INSTANT BUY\nEntry: 5177/5175
    # XAUUSD SELL NOW: (5416 5420)
    # Gold Buy 4898-         ou  Gold Buy NOW 4865 4862
    RE_MAIN = re.compile(
        r'(XAUUSD|GOLD|XAU/USD|XAGUSD|SILVER|USOIL|OIL)\s+(?:INSTANT\s+|NOW[:\s]*)?(?:INSTANT\s+)?(BUY|SELL)[^\d\n]{0,30}?'
        r'(?:Entry:\s*)?\(?\s*([\d.]+)\s*[-/\s]\s*([\d.]+)?\s*\)?',
        re.IGNORECASE
    )

    # 🔴 SELL TRADE XAU/USD  (direction avant le symbole)
    RE_MAIN_ALT = re.compile(
        r'(BUY|SELL)\s+TRADE\s+(XAUUSD|GOLD|XAU/USD)',
        re.IGNORECASE
    )

    # Zone seule sur ligne : 5009.5- 5012.0  ou  5009.5/5012.0
    RE_ZONE_LINE = re.compile(
        r'^\s*([\d.]+)\s*[-/]\s*([\d.]+)\s*$',
        re.MULTILINE
    )

    # Target: open  → TPs automatiques
    RE_TARGET_OPEN = re.compile(
        r'Target\s*:\s*open',
        re.IGNORECASE
    )

    # Stop Loss avec 📍 ou texte
    RE_SL_TARGET = re.compile(
        r'(?:📍\s*)?Stop\s+Loss\s*:\s*([\d.]+)',
        re.IGNORECASE
    )

    # TP standard : TP1: 5181 / TP 5218 / 🎯TP 5191 / TP¹.5225
    # TP court    : TP-4900 / TP-4910-max4925
    RE_TP = re.compile(
        r'(?:\U0001F3AF|\U0001F4CA|\u27A4|\u25BA|\u25B6)?\s*'
        r'TP[\u00b9\u00b2\u00b3\u2074\u2075\d]*[-.:]\s*([\d]{3,}(?:\.\d+)?)(?:-max[\d.]+)?',
        re.IGNORECASE
    )

    # TAKE PROFIT: 1   (5412)  ou  TAKE PROFIT: 1  5412
    # TAKE PROFIT. 4867        ou  TAKE PROFIT. 4870 CONFIRM✅
    RE_TP_LONG = re.compile(
        r'TAKE\s+PROFIT\s*[:.]\s*(?:\d+\s+)?\(?\s*([\d]{4,}(?:\.\d+)?)\s*\)?(?:\s*CONFIRM\S*)?',
        re.IGNORECASE
    )

    # SL standard : SL : 5168 / 🔴SL ..5240
    # SL triple   : SL---4995
    RE_SL = re.compile(
        r'(?:\U0001F534|\u274C|\U0001F6D1|\u26D4|\U0001F44E)?\s*'
        r'SL[-.\s]{0,5}([\d]{3,}(?:\.\d+)?)',
        re.IGNORECASE
    )

    # STOP LOSS: (5429)  ou  STOP LOSS: 5429  ou  STOP LOSS. 4856
    RE_SL_LONG = re.compile(
        r'STOP\s+LOSS\s*[:.]\s*\(?\s*([\d]{3,}(?:\.\d+)?)\s*\)?',
        re.IGNORECASE
    )

    # SL MOVE / MOVE SL / New SL / SL → / SL moved to
    RE_SL_MOVE = re.compile(
        r'(?:SL\s*MOVE|MOVE\s*SL|New\s*SL|SL\s*\u2192|SL\s*moved?\s*to)\s*[:\s]*\s*([\d.]+)',
        re.IGNORECASE
    )

    # SL seul sur une ligne
    RE_SL_ALONE = re.compile(
        r'^\s*(?:\U0001F534|\u274C|\U0001F6D1)?\s*SL\s*[.:\s]+\s*([\d]{3,}(?:\.\d+)?)\s*$',
        re.IGNORECASE | re.MULTILINE
    )

    RE_CLOSE = re.compile(r'close\s+(all|[A-Z]{3,10})', re.IGNORECASE)

    def parse(self, text: str) -> dict | None:

        # 1. CLOSE
        close_m = self.RE_CLOSE.search(text.upper())
        if close_m:
            target = close_m.group(1).upper()
            return {
                "type":      "CLOSE",
                "symbol":    None if target == "ALL" else target,
                "close_all": target == "ALL",
            }

        # 2. SL MOVE (avant filtre spam car "moved to" peut être dans un signal)
        sl_move_m = self.RE_SL_MOVE.search(text)
        if sl_move_m:
            return {"type": "SL_MOVE", "new_sl": float(sl_move_m.group(1))}

        # 3. Filtre spam
        if is_spam(text):
            log.debug(f"[SPAM] Message filtré: {text[:60].replace(chr(10),' ')}")
            return None

        # 4. BUY / SELL
        main_m = self.RE_MAIN.search(text)

        # Format alternatif : "🔴 SELL TRADE XAU/USD \n 5009.5- 5012.0"
        if not main_m:
            alt_m = self.RE_MAIN_ALT.search(text)
            if alt_m:
                zone_m = self.RE_ZONE_LINE.search(text)
                if zone_m:
                    action    = alt_m.group(1).upper()
                    symbol    = self.SYMBOL_MAP.get(alt_m.group(2).upper().replace("/",""), alt_m.group(2).upper().replace("/",""))
                    price_a   = float(zone_m.group(1))
                    price_b   = float(zone_m.group(2))
                    zone_low  = min(price_a, price_b)
                    zone_high = max(price_a, price_b)
                    zone_mid  = round((zone_low + zone_high) / 2, 2)

                    # SL
                    sl_m = self.RE_SL_TARGET.search(text) or self.RE_SL_LONG.search(text) or self.RE_SL.search(text)
                    sl   = float(sl_m.group(1)) if sl_m else None

                    if sl is None:
                        log.warning(f"Format TRADE — SL manquant | {text[:80]}")
                        return None

                    # TPs automatiques si "Target: open"
                    tps = []
                    if self.RE_TARGET_OPEN.search(text):
                        step = 4.0
                        base = zone_high if action == "BUY" else zone_low
                        if action == "SELL":
                            tps = [
                                round(base - step,     2),   # TP1
                                round(base - step * 2, 2),   # TP2
                                round(base - step * 3, 2),   # TP3
                            ]
                        else:  # BUY
                            tps = [
                                round(base + step,     2),   # TP1
                                round(base + step * 2, 2),   # TP2
                                round(base + step * 3, 2),   # TP3
                            ]
                        log.info(
                            f"Target:open → TPs auto générés (step=$4) "
                            f"TP1={tps[0]} TP2={tps[1]} TP3={tps[2]} TP4=Trail"
                        )
                    else:
                        # TPs explicites dans le message
                        for val in self.RE_TP.findall(text):
                            try: tps.append(float(val))
                            except ValueError: pass
                        if not tps:
                            for val in self.RE_TP_LONG.findall(text):
                                try: tps.append(float(val))
                                except ValueError: pass

                    if not tps:
                        log.warning(f"Format TRADE — TPs manquants | {text[:80]}")
                        return None

                    tp1      = tps[0]
                    tp2      = tps[1] if len(tps) >= 2 else tps[-1]
                    tp3      = tps[2] if len(tps) >= 3 else tps[-1]
                    tp4      = tps[3] if len(tps) >= 4 else tps[-1]
                    tp_final = tps[-1]   # TP4 = trail (laisser courir)

                    log.info(
                        f"Signal TRADE → {action} {symbol} "
                        f"zone [{zone_low}—{zone_mid}—{zone_high}] "
                        f"TP1={tp1} TP2={tp2} TP3={tp3} TP4=Trail SL={sl}"
                    )

                    return {
                        "type":      "TRADE",
                        "symbol":    symbol,
                        "action":    action,
                        "zone_low":  zone_low,
                        "zone_mid":  zone_mid,
                        "zone_high": zone_high,
                        "tps":       tps,
                        "tp1":       tp1,
                        "tp2":       tp2,
                        "tp3":       tp3,
                        "tp4":       tp4,
                        "tp_final":  tp_final,
                        "sl":        sl,
                    }

        if not main_m:
            sl_alone = self.RE_SL_ALONE.search(text)
            if sl_alone:
                return {"type": "SL_MOVE", "new_sl": float(sl_alone.group(1))}
            return None

        symbol  = self.SYMBOL_MAP.get(main_m.group(1).upper(), main_m.group(1).upper())
        action  = main_m.group(2).upper()
        price_a = float(main_m.group(3))
        price_b = float(main_m.group(4))

        zone_low  = min(price_a, price_b)
        zone_high = max(price_a, price_b)
        zone_mid  = round((zone_low + zone_high) / 2, 2)

        # TPs : essayer les deux formats
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

        # SL : essayer les deux formats
        sl_m = self.RE_SL.search(text)
        if not sl_m:
            sl_m = self.RE_SL_LONG.search(text)
        sl = float(sl_m.group(1)) if sl_m else None

        if not tps or sl is None:
            log.warning(f"Signal incomplet — TPs={tps} SL={sl} | {text[:80]}")
            return None

        # Calcul des niveaux internes
        # TP1 du signal est toujours disponible
        tp1 = tps[0]

        if len(tps) == 3:
            # TP1 → tp2 bot, TP2 ignoré, TP3 → tp_final
            # tp3 et tp4 générés entre TP1 et TP3
            tp2      = tps[0]
            tp_final = tps[2]
            distance = abs(tp_final - tp2)
            step     = distance / 3
            if tp2 > tp_final:  # SELL
                tp3 = round(tp2 - step,     2)
                tp4 = round(tp2 - step * 2, 2)
            else:               # BUY
                tp3 = round(tp2 + step,     2)
                tp4 = round(tp2 + step * 2, 2)
            log.info(
                f"3 TPs → TP1={tp2}(seuil BE) "
                f"tp3={tp3}(généré) tp4={tp4}(généré) "
                f"TPfinal={tp_final} | TP2 signal ignoré"
            )
        else:
            tp2      = tps[1] if len(tps) >= 2 else tps[-1]
            tp3      = tps[2] if len(tps) >= 3 else tps[-1]
            tp4      = tps[3] if len(tps) >= 4 else tps[-1]
            tp_final = tps[-1]

        log.info(
            f"Signal parsé → {action} {symbol} "
            f"zone [{zone_low}—{zone_mid}—{zone_high}] "
            f"TP1={tp1} TP2={tp2} TP3={tp3} TP4={tp4} TPfinal={tp_final} SL={sl} "
            f"({len(tps)} TPs)"
        )

        return {
            "type":      "TRADE",
            "symbol":    symbol,
            "action":    action,
            "zone_low":  zone_low,
            "zone_mid":  zone_mid,
            "zone_high": zone_high,
            "tps":       tps,
            "tp1":       tp1,
            "tp2":       tp2,
            "tp3":       tp3,
            "tp4":       tp4,
            "tp_final":  tp_final,
            "sl":        sl,
        }


# ------------------------------------------------------------------
#  BRIDGE MT5
# ------------------------------------------------------------------
class MT5Bridge:

    _sym_cache: dict = {}

    def connect(self) -> bool:
        if mt5.initialize():
            info = mt5.account_info()
            if info and info.login > 0:
                log.info(f"MT5 déjà connecté → {info.name} | Balance: {info.balance} {info.currency}")
                return self._check_algo()
            mt5.shutdown()

        if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            log.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        log.info(f"MT5 connecté → {info.name} | Balance: {info.balance} {info.currency}")
        return self._check_algo()

    def _check_algo(self) -> bool:
        terminal = mt5.terminal_info()
        try:
            algo_ok = bool(getattr(terminal, 'trade_expert', True))
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
            for sfx in ["m", "m+", ".a", "pro", "+", ".", "z", "micro", "#", ""]:
                candidate = symbol + sfx
                info = mt5.symbol_info(candidate)
                if info:
                    log.info(f"Symbole résolu : {symbol} → {candidate}")
                    break

        if info is None and symbol.endswith("m"):
            info = mt5.symbol_info(symbol[:-1])
            if info:
                log.info(f"Symbole résolu : {symbol} → {symbol[:-1]}")

        if info is None:
            all_syms = mt5.symbols_get()
            if all_syms:
                matches = [s for s in all_syms if s.name.upper().startswith(symbol.upper()[:6])]
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

    def current_price(self, symbol: str, action: str) -> float | None:
        sym_info = self._sym(symbol)
        if sym_info is None:
            return None
        tick = mt5.symbol_info_tick(sym_info.name)
        if not tick:
            return None
        return tick.ask if action == "BUY" else tick.bid

    def place_market_order(self, signal: dict, lot: float, tp: float) -> int | None:
        sym = self._sym(signal["symbol"])
        if not sym:
            return None
        action     = signal["action"]
        tick       = mt5.symbol_info_tick(sym.name)
        if not tick:
            log.error(f"Tick introuvable pour {sym.name}")
            return None
        price      = tick.ask if action == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        result = mt5.order_send({
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       sym.name,
            "volume":       lot,
            "type":         order_type,
            "price":        price,
            "sl":           round(signal["sl"], sym.digits),
            "tp":           round(tp, sym.digits),
            "deviation":    SLIPPAGE,
            "magic":        MAGIC_NUMBER,
            "comment":      f"TG-market {datetime.now():%H:%M}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        })

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"MARKET {action} {sym.name} lot={lot} @{price} ticket#{result.order}")
            return result.order
        log.error(f"Market échoué | retcode={result.retcode if result else 'N/A'}")
        return None

    def place_limit_order(self, signal: dict, lot: float, price: float,
                          tp: float, expiry: datetime) -> int | None:
        sym = self._sym(signal["symbol"])
        if not sym:
            return None
        action     = signal["action"]

        # Vérifier que le TP est valide
        if action == "BUY" and tp <= price:
            log.warning(f"LIMIT BUY ignoré @{price} — TP={tp} <= prix d'entrée")
            return None
        if action == "SELL" and tp >= price:
            log.warning(f"LIMIT SELL ignoré @{price} — TP={tp} >= prix d'entrée")
            return None

        order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

        result = mt5.order_send({
            "action":       mt5.TRADE_ACTION_PENDING,
            "symbol":       sym.name,
            "volume":       lot,
            "type":         order_type,
            "price":        round(price, sym.digits),
            "sl":           round(signal["sl"], sym.digits),
            "tp":           round(tp, sym.digits),
            "deviation":    SLIPPAGE,
            "magic":        MAGIC_NUMBER,
            "comment":      f"TG-limit {datetime.now():%H:%M}",
            "type_time":    mt5.ORDER_TIME_SPECIFIED,
            "expiration":   int(expiry.timestamp()),
            "type_filling": mt5.ORDER_FILLING_RETURN,
        })

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"LIMIT {action} {sym.name} lot={lot} @{price} TP={tp} order#{result.order}")
            return result.order
        log.error(f"Limit échoué @{price} | retcode={result.retcode if result else 'N/A'}")
        return None

    def cancel_order(self, order_ticket: int) -> bool:
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order_ticket})
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"{'OK' if ok else 'FAIL'} Annulation ordre #{order_ticket}")
        return ok

    def close_position(self, ticket: int, comment: str = "close") -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos  = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        close_type  = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

        result = mt5.order_send({
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     ticket,
            "price":        close_price,
            "deviation":    SLIPPAGE,
            "magic":        MAGIC_NUMBER,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        })
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"{'OK' if ok else 'FAIL'} Fermeture #{ticket} ({comment}) P&L={pos.profit:.2f}")
        return ok

    def modify_sl(self, ticket: int, new_sl: float, label: str = "") -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        sym = mt5.symbol_info(pos.symbol)
        if sym is None:
            return False
        result = mt5.order_send({
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   pos.symbol,
            "position": ticket,
            "sl":       round(new_sl, sym.digits),
            "tp":       pos.tp,
        })
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info(f"SL modifié #{ticket} → {new_sl} {label}")
        return ok

    def modify_tp(self, ticket: int, new_tp: float, new_sl: float) -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        sym = mt5.symbol_info(pos.symbol)
        if sym is None:
            return False
        result = mt5.order_send({
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   pos.symbol,
            "position": ticket,
            "sl":       round(new_sl, sym.digits),
            "tp":       round(new_tp, sym.digits),
        })
        return result and result.retcode == mt5.TRADE_RETCODE_DONE

    def update_sl_all(self, new_sl: float):
        """SL MOVE : met à jour SL sur toutes les positions ET ordres limits."""
        updated = 0

        positions = mt5.positions_get()
        if positions:
            for pos in positions:
                if pos.magic != MAGIC_NUMBER:
                    continue
                sym = mt5.symbol_info(pos.symbol)
                if not sym:
                    continue
                result = mt5.order_send({
                    "action":   mt5.TRADE_ACTION_SLTP,
                    "symbol":   pos.symbol,
                    "position": pos.ticket,
                    "sl":       round(new_sl, sym.digits),
                    "tp":       pos.tp,
                })
                ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
                log.info(f"{'OK' if ok else 'FAIL'} SL MOVE position #{pos.ticket} → {new_sl}")
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
                result = mt5.order_send({
                    "action":     mt5.TRADE_ACTION_MODIFY,
                    "order":      order.ticket,
                    "price":      order.price_open,
                    "sl":         round(new_sl, sym.digits),
                    "tp":         order.tp,
                    "type_time":  order.type_time,
                    "expiration": order.time_expiration,
                })
                ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
                log.info(f"{'OK' if ok else 'FAIL'} SL MOVE ordre #{order.ticket} → {new_sl}")
                if ok:
                    updated += 1

        log.info(f"SL MOVE appliqué sur {updated} position(s)/ordre(s) → SL={new_sl}")

    def close_all(self, symbol: str | None = None):
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            return
        for pos in positions:
            if pos.magic == MAGIC_NUMBER:
                self.close_position(pos.ticket, comment="close-all")


# ------------------------------------------------------------------
#  DÉTECTION DE CONFLIT
# ------------------------------------------------------------------
def check_conflict(signal: dict, bridge: MT5Bridge, manager) -> bool:
    symbol     = signal["symbol"]
    new_action = signal["action"]
    opposite   = "SELL" if new_action == "BUY" else "BUY"
    conflict   = False

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
            if entry["signal"]["symbol"] == symbol and entry["signal"]["action"] == opposite:
                conflict = True
                break

    if not conflict:
        return False

    log.warning(f"CONFLIT {symbol} : entrant={new_action} existant={opposite} → tout fermé, signal ignoré")

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


# ------------------------------------------------------------------
#  EXÉCUTION DU SIGNAL
# ------------------------------------------------------------------
def execute_signal(signal: dict, bridge: MT5Bridge, manager):
    action    = signal["action"]
    symbol    = signal["symbol"]
    zone_low  = signal["zone_low"]
    zone_mid  = signal["zone_mid"]
    zone_high = signal["zone_high"]
    tp1       = signal["tp1"]
    tp2       = signal["tp2"]
    tp3       = signal["tp3"]
    tp_final  = signal["tp_final"]
    sl        = signal["sl"]
    expiry    = datetime.now() + timedelta(minutes=ORDER_EXPIRY_MIN)

    if check_conflict(signal, bridge, manager):
        return

    sym_info = bridge._sym(symbol)
    if sym_info is None:
        log.error(f"Symbole introuvable : {symbol}")
        return

    current = bridge.current_price(sym_info.name, action)
    if current is None:
        log.error(f"Impossible de lire le prix de {sym_info.name}")
        return

    in_zone = zone_low <= current <= zone_high

    log.info("=" * 55)
    log.info(f"SIGNAL {action} {symbol}")
    log.info(f"Zone [{zone_low} — {zone_mid} — {zone_high}] | Prix={current}")
    log.info(f"{'DANS la zone → CAS 1' if in_zone else 'HORS zone → CAS 2'}")
    log.info(f"TP1={tp1} TP2={tp2} TP3={tp3} TPfinal={tp_final} SL={sl}")
    log.info("=" * 55)

    orders  = []
    tickets = []

    if in_zone:
        # ── CAS 1 ─────────────────────────────────────────────────
        t1 = bridge.place_market_order(signal, 0.01, tp=tp2)
        if t1:
            tickets.append({
                "ticket":         t1,
                "lot":            0.01,
                "role":           "market",
                "entry_price":    current,
                "tp1":            tp1,
                "tp2":            tp2,
                "tp3":            tp3,
                "tp4":            signal["tp4"],
                "tp_final":       tp_final,
                # Escalier BE
                "sl_step":        0,   # 0=initial 1=BE 2=TP1 3=TP2 4=trail
                "trail_active":   False,
                "limit_executed": False,
            })

        limit_price = zone_high if action == "SELL" else zone_low
        o1 = bridge.place_limit_order(signal, 0.01, limit_price, tp_final, expiry)
        if o1:
            orders.append({
                "order":       o1,
                "lot":         0.01,
                "price":       limit_price,
                "role":        "limit_cas1",
                "tp1":         tp1,
                "tp2":         tp2,
                "tp3":         tp3,
                "tp4":         signal["tp4"],
                "tp_final":    tp_final,
                "sl_step":     0,
                "trail_active": False,
            })

        log.info(f"CAS 1 → MARKET 0.01 TP={tp2} | LIMIT 0.01 @{limit_price} TP={tp_final}")

    else:
        # ── CAS 2 ─────────────────────────────────────────────────
        # BUY  : 1er exécuté = zone_high, meilleur prix = zone_low
        # SELL : 1er exécuté = zone_low,  meilleur prix = zone_high

        # Prix du 3ème ordre = limite zone + 1/3 de la distance vers le SL
        if action == "BUY":
            dist_to_sl = zone_low - sl
            price_3rd  = round(zone_low - dist_to_sl / 3, sym_info.digits)
            orders_def = [
                {"price": zone_high, "tp": tp2,      "role": "limit_high"},
                {"price": zone_mid,  "tp": tp3,      "role": "limit_mid"},
                {"price": price_3rd, "tp": tp_final, "role": "limit_low"},
            ]
        else:  # SELL
            dist_to_sl = sl - zone_high
            price_3rd  = round(zone_high + dist_to_sl / 3, sym_info.digits)
            orders_def = [
                {"price": price_3rd, "tp": tp_final, "role": "limit_high"},
                {"price": zone_mid,  "tp": tp3,      "role": "limit_mid"},
                {"price": zone_low,  "tp": tp2,      "role": "limit_low"},
            ]

        for od in orders_def:
            o = bridge.place_limit_order(signal, 0.01, od["price"], od["tp"], expiry)
            if o:
                orders.append({
                    "order":       o,
                    "lot":         0.01,
                    "price":       od["price"],
                    "role":        od["role"],
                    "tp1":         tp1,
                    "tp2":         tp2,
                    "tp3":         tp3,
                    "tp4":         signal["tp4"],
                    "tp_final":    tp_final,
                    "sl_step":     0,
                    "trail_active": False,
                })

        log.info(
            f"CAS 2 → "
            + " | ".join(f"LIMIT @{od['price']} TP={od['tp']} ({od['role']})" for od in orders_def)
        )

        # Rebond info
        tp2_sig = signal["tp2"]
        tp3_sig = signal["tp3"]
        if action == "BUY":
            seuil_bas    = tp2_sig if current >= tp2_sig else None
            seuil_rebond = tp3_sig
        else:
            seuil_bas    = tp2_sig if current <= tp2_sig else None
            seuil_rebond = tp3_sig

        rebond_info = {
            "actif":        True,
            "seuil_bas":    seuil_bas,
            "seuil_rebond": seuil_rebond,
            "bas_touche":   seuil_bas is None,
        }
        log.info(
            f"Rebond surveillé — seuil_bas={seuil_bas} seuil_rebond={seuil_rebond} "
            f"(bas_touche={'oui' if rebond_info['bas_touche'] else 'non'})"
        )

    if not orders and not tickets:
        log.error("Aucun ordre placé.")
        return

    manager.register({
        "signal":      signal,
        "orders":      orders,
        "tickets":     tickets,
        "expiry":      expiry,
        "rebond_info": rebond_info if not in_zone else {"actif": False},
    })


# ------------------------------------------------------------------
#  TRADE MANAGER
# ------------------------------------------------------------------
class TradeManager:

    def __init__(self, bridge: MT5Bridge):
        self.bridge  = bridge
        self.active  = []
        self._stop   = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def register(self, entry: dict):
        self.active.append(entry)
        sig = entry["signal"]
        log.info(
            f"TradeManager: {sig['action']} {sig['symbol']} enregistré | "
            f"{len(entry['orders'])} ordres | {len(entry['tickets'])} market"
        )

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            time.sleep(10)
            try:
                log.debug("[CYCLE] TradeManager check")
                self._check_all()
            except Exception as exc:
                log.error(f"TradeManager erreur: {exc}")

    def _check_all(self):
        now       = datetime.now()
        to_remove = []

        for entry in self.active:
            sig    = entry["signal"]
            symbol = sig["symbol"]
            action = sig["action"]

            # ── 1. Résoudre ordres limits → positions ─────────────
            still_pending = []
            for o in entry["orders"]:
                pos = self._resolve_order(o["order"], symbol)
                if pos:
                    tk = {
                        "ticket":       pos.ticket,
                        "lot":          o["lot"],
                        "role":         o["role"],
                        "entry_price":  pos.price_open,
                        "tp1":          o["tp1"],
                        "tp2":          o["tp2"],
                        "tp3":          o["tp3"],
                        "tp4":          o["tp4"],
                        "tp_final":     o["tp_final"],
                        "sl_step":      0,
                        "trail_active": False,
                    }
                    entry["tickets"].append(tk)
                    log.info(f"Ordre #{o['order']} rempli → ticket={pos.ticket} @{pos.price_open} ({o['role']})")

                    # CAS 1 : LIMIT exécuté → signaler au MARKET
                    if o["role"] == "limit_cas1":
                        for t in entry["tickets"]:
                            if t.get("role") == "market":
                                t["limit_executed"] = True
                                log.info(f"LIMIT CAS1 exécuté → MARKET #{t['ticket']} : sera fermé au TP2")

                elif now > entry["expiry"]:
                    self.bridge.cancel_order(o["order"])
                    log.info(f"Ordre #{o['order']} expiré, annulé ({o['role']})")
                else:
                    still_pending.append(o)
            entry["orders"] = still_pending

            # Vérifier s'il reste quelque chose
            active_tks = [t for t in entry["tickets"] if self._get_pos(t["ticket"])]
            if not entry["orders"] and not active_tks:
                to_remove.append(entry)
                continue

            # ── 2. Prix courant ────────────────────────────────────
            sym_info = self.bridge._sym(symbol)
            if sym_info is None:
                continue
            tick = mt5.symbol_info_tick(sym_info.name)
            if tick is None:
                log.warning(f"Prix indisponible pour {sym_info.name}")
                continue
            current = tick.bid if action == "BUY" else tick.ask

            # ── 3. Rebond CAS 2 ────────────────────────────────────
            ri = entry.get("rebond_info", {})
            if ri.get("actif") and not entry["tickets"]:
                seuil_bas    = ri["seuil_bas"]
                seuil_rebond = ri["seuil_rebond"]

                if not ri["bas_touche"] and seuil_bas is not None:
                    if (action == "BUY"  and current <= seuil_bas) or \
                       (action == "SELL" and current >= seuil_bas):
                        ri["bas_touche"] = True
                        log.info(f"Rebond ({symbol}) — seuil bas {seuil_bas} touché")

                if ri["bas_touche"]:
                    rebond = (action == "BUY"  and current >= seuil_rebond) or \
                             (action == "SELL" and current <= seuil_rebond)
                    if rebond:
                        log.info(f"Rebond confirmé ({symbol}) prix={current} > TP3={seuil_rebond} → annulation ordres")
                        for o in entry["orders"]:
                            self.bridge.cancel_order(o["order"])
                        entry["orders"] = []
                        to_remove.append(entry)
                        continue

            # ── 4. Gestion BE escalier + Trail ────────────────────
            for t in entry["tickets"]:
                pos = self._get_pos(t["ticket"])
                if pos is None:
                    continue

                ep       = t.get("entry_price") or pos.price_open
                tp1_t    = t.get("tp1", ep)
                tp2_t    = t.get("tp2")
                tp3_t    = t.get("tp3")
                tp4_t    = t.get("tp4")
                tp_final = t.get("tp_final")
                sl_step  = t.get("sl_step", 0)

                # ─ CAS 1 MARKET : logique TP2 inversée ────────────
                if t.get("role") == "market" and sl_step == 0 and tp2_t:
                    tp2_reached = (action == "BUY"  and current >= tp2_t) or \
                                  (action == "SELL" and current <= tp2_t)
                    if tp2_reached:
                        if not t.get("limit_executed", False):
                            # LIMIT non exécuté → MARKET passe BE + Trail
                            t["sl_step"]      = 1
                            t["trail_active"] = False  # trail déclenché à tp4
                            self.bridge.modify_sl(t["ticket"], ep, label="[BE CAS1 market→trail]")
                            # Annuler le LIMIT
                            for o in entry["orders"]:
                                if o.get("role") == "limit_cas1":
                                    self.bridge.cancel_order(o["order"])
                                    log.info(f"TP2 atteint → LIMIT annulé, MARKET passe BE+Trail")
                        else:
                            # LIMIT exécuté → MARKET fermé au TP2
                            self.bridge.close_position(t["ticket"], comment="TP2-CAS1")
                            log.info(f"TP2 atteint → MARKET #{t['ticket']} fermé (LIMIT actif)")
                        continue

                # ─ Escalier BE pour tous les autres tickets ────────
                if sl_step == 0 and tp2_t:
                    reached = (action == "BUY"  and current >= tp2_t) or \
                              (action == "SELL" and current <= tp2_t)
                    if reached:
                        ok = self.bridge.modify_sl(t["ticket"], ep, label="[BE étape1 @entrée]")
                        if ok:
                            t["sl_step"] = 1
                            log.info(f"Escalier BE #{t['ticket']} : SL → entrée {ep}")

                elif sl_step == 1 and tp3_t:
                    reached = (action == "BUY"  and current >= tp3_t) or \
                              (action == "SELL" and current <= tp3_t)
                    if reached:
                        ok = self.bridge.modify_sl(t["ticket"], tp1_t, label="[BE étape2 @TP1]")
                        if ok:
                            t["sl_step"] = 2
                            log.info(f"Escalier BE #{t['ticket']} : SL → TP1 {tp1_t}")

                elif sl_step == 2 and tp4_t:
                    reached = (action == "BUY"  and current >= tp4_t) or \
                              (action == "SELL" and current <= tp4_t)
                    if reached:
                        ok = self.bridge.modify_sl(t["ticket"], tp2_t, label="[BE étape3 @TP2]")
                        if ok:
                            t["sl_step"]      = 3
                            t["trail_active"] = False  # sera activé à TPfinal
                            log.info(f"Escalier BE #{t['ticket']} : SL → TP2 {tp2_t}")

                elif sl_step == 3 and tp_final:
                    reached = (action == "BUY"  and current >= tp_final) or \
                              (action == "SELL" and current <= tp_final)
                    if reached:
                        t["sl_step"]      = 4
                        t["trail_active"] = True
                        log.info(f"Trail activé #{t['ticket']} — dépassé TPfinal {tp_final}")

                # ─ Trail SL dynamique (en pips) ───────────────────
                if t.get("trail_active"):
                    pos_live = self._get_pos(t["ticket"])
                    if pos_live:
                        sym2 = mt5.symbol_info(pos_live.symbol)
                        if sym2 is None:
                            continue
                        trail_gap = TRAIL_POINTS * sym2.point * 10
                        if action == "BUY":
                            new_sl = current - trail_gap
                            if new_sl > pos_live.sl:
                                self.bridge.modify_sl(t["ticket"], new_sl, label="[Trail BUY]")
                        else:
                            new_sl = current + trail_gap
                            if new_sl < pos_live.sl or pos_live.sl == 0:
                                self.bridge.modify_sl(t["ticket"], new_sl, label="[Trail SELL]")

            # ── 5. CAS 2 : scénarios A/B/C quand 1er ordre atteint TP2
            self._handle_cas2_scenarios(entry, action, symbol, current)

            # ── 6. Trade terminé ──────────────────────────────────
            active_tks = [t for t in entry["tickets"] if self._get_pos(t["ticket"])]
            if not entry["orders"] and not active_tks:
                log.info(f"Trade terminé ({symbol})")
                to_remove.append(entry)

        for e in to_remove:
            if e in self.active:
                self.active.remove(e)

    def _handle_cas2_scenarios(self, entry, action, symbol, current):
        """Gère les scénarios A/B/C du CAS 2 quand le 1er ordre atteint TP2."""
        sig = entry["signal"]

        # Chercher le 1er ordre exécuté (limit_high BUY / limit_low SELL)
        trigger_role = "limit_high" if action == "BUY" else "limit_low"
        trigger_tk   = next((t for t in entry["tickets"] if t.get("role") == trigger_role), None)
        if not trigger_tk:
            return

        # Déjà traité
        if trigger_tk.get("cas2_done"):
            return

        tp2_t = trigger_tk.get("tp2")
        if not tp2_t:
            return

        tp2_reached = (action == "BUY"  and current >= tp2_t) or \
                      (action == "SELL" and current <= tp2_t)
        if not tp2_reached:
            return

        trigger_tk["cas2_done"] = True

        mid_tk     = next((t for t in entry["tickets"] if t.get("role") == "limit_mid"), None)
        other_role = "limit_low" if action == "BUY" else "limit_high"
        other_tk   = next((t for t in entry["tickets"] if t.get("role") == other_role), None)
        sym        = self.bridge._sym(symbol)

        if not mid_tk and not other_tk:
            # Scénario A : seul le 1er rempli
            for o in entry["orders"]:
                if o.get("role") in ["limit_mid", other_role]:
                    self.bridge.cancel_order(o["order"])
            # 1er passe BE + trail (déjà géré par escalier)
            log.info(f"ScénA ({symbol}) → 1er seul rempli, mid+other annulés")

        elif mid_tk and not other_tk:
            # Scénario B : 1er + mid
            for o in entry["orders"]:
                if o.get("role") == other_role:
                    self.bridge.cancel_order(o["order"])
            # 1er fermé au TP2
            self.bridge.close_position(trigger_tk["ticket"], comment="ScénB-TP2")
            # mid passe BE
            if sym and self._get_pos(mid_tk["ticket"]):
                mid_ep = mid_tk.get("entry_price", mid_tk.get("tp2", 0))
                self.bridge.modify_sl(mid_tk["ticket"], mid_ep, label="[BE ScénB]")
                mid_tk["sl_step"] = 1
            log.info(f"ScénB ({symbol}) → 1er fermé TP2, mid BE+continue")

        elif mid_tk and other_tk:
            # Scénario C : les 3 remplis
            # 1er fermé au TP2
            self.bridge.close_position(trigger_tk["ticket"], comment="ScénC-TP2")
            # mid passe BE + TP3
            if sym and self._get_pos(mid_tk["ticket"]):
                mid_ep = mid_tk.get("entry_price", mid_tk.get("tp2", 0))
                self.bridge.modify_sl(mid_tk["ticket"], mid_ep, label="[BE ScénC mid]")
                mid_tk["sl_step"] = 1
            # other passe BE + trail
            if sym and self._get_pos(other_tk["ticket"]):
                other_ep = other_tk.get("entry_price", other_tk.get("tp2", 0))
                self.bridge.modify_sl(other_tk["ticket"], other_ep, label="[BE ScénC other]")
                other_tk["sl_step"]      = 1
                other_tk["trail_active"] = False  # trail à TPfinal
            log.info(f"ScénC ({symbol}) → 1er fermé TP2, mid BE+TP3, other BE+trail")

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


# ------------------------------------------------------------------
#  MAIN
# ------------------------------------------------------------------
async def main():
    parser  = SignalParser()
    bridge  = MT5Bridge()
    manager = None

    if not bridge.connect():
        log.critical("Bot arrêté — corrigez MT5 puis relancez.")
        return

    manager     = TradeManager(bridge)
    news_mgr    = NewsManager(bridge)
    news_mgr.set_manager(manager)

    client = TelegramClient("session_trading", API_ID, API_HASH)
    await client.start()
    log.info("Telegram connecté.")

    entity1 = await client.get_entity(CHANNEL_NAME)
    log.info(f"Canal 1 : {getattr(entity1, 'title', CHANNEL_NAME)}")

    entity2 = None
    if CHANNEL_NAME_2:
        try:
            entity2 = await client.get_entity(CHANNEL_NAME_2)
            log.info(f"Canal 2 : {getattr(entity2, 'title', CHANNEL_NAME_2)}")
        except Exception as e:
            log.warning(f"Canal 2 introuvable ({CHANNEL_NAME_2}) : {e}")

    chats = [entity1] + ([entity2] if entity2 else [])

    @client.on(events.NewMessage(chats=chats))
    async def handler(event):
        text       = event.message.text or ""
        chat       = await event.get_chat()
        canal_name = getattr(chat, "title", "inconnu")

        # Filtre spam console
        if is_spam(text):
            log.debug(f"[SPAM] [{canal_name}] {text[:60].replace(chr(10),' ')}")
            return

        log.info(f"[{canal_name}] {text[:150].replace(chr(10), ' | ')}")

        signal = parser.parse(text)
        if signal is None:
            return

        if signal["type"] == "CLOSE":
            bridge.close_all(symbol=signal.get("symbol"))

        elif signal["type"] == "SL_MOVE":
            log.info(f"SL MOVE reçu → nouveau SL={signal['new_sl']}")
            bridge.update_sl_all(signal["new_sl"])

        elif signal["type"] == "TRADE":
            # Vérifier fenêtre horaire bloquée
            blocked, desc = in_blocked_window()
            if blocked:
                log.info(f"[TIME] Signal ignoré — fenêtre bloquée {desc}")
                return

            # Vérifier news
            if NEWS_ENABLED and news_mgr.is_blocked():
                log.info(f"[NEWS] Signal ignoré — protection news active")
                return

            execute_signal(signal, bridge, manager)

    log.info("=" * 55)
    log.info("   BOT ACTIF")
    log.info(f"   Canal 1 : {CHANNEL_NAME}")
    if CHANNEL_NAME_2:
        log.info(f"   Canal 2 : {CHANNEL_NAME_2}")
    log.info(f"   Trail SL : {TRAIL_POINTS} pips après TPfinal")
    log.info(f"   News filter : {'ON' if NEWS_ENABLED else 'OFF'}")
    log.info(f"   Time filter : {'ON' if TIME_FILTER_ENABLED else 'OFF'}")
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
