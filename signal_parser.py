"""
Signal Parser V5.1 — Amélioré
Supporte les formats réels des canaux Telegram trading.

Formats supportés:
  1. DAILY SIGNAL: "XAUUSD DAILY SIGNAL" + Pair/Action/Entry/TP/SL structurés
  2. SELL NOW: "XAUUSD Sell NOW 4659/4662" + TAKE PROFIT / STOP LOSS
  3. BUY zone: "XAUUSD (GOLD) BUY 4630-4625" + TPⁿ / SL
  4. SMC: "💎XAU/USD SELL 4630/4638" + TP¹²³ / SL_
  5. SL_MOVE: "SL MOVE 4650", "New SL: 4650", etc.
  6. CLOSE: "close all", "close XAUUSD"
  7. Entry labelé: "ENTRY: 3240", "@ 3240", "BUY 3240" (sans range)

Améliorations v5.1:
  - Entry sans range (ENTRY:, @, BUY/SELL inline, ZONE:)
  - 10 patterns TP (+TP.1:, parenthèses, TAKE PROFIT ONE/TWO/THREE, TARGET)
  - 10 patterns SL (+SL BREAKOUT, parenthèses, STOP:, SL. avec point)
  - Superscripts dans TP (TP¹, TP², TPⁿ) améliorés
"""

import re
import logging

log = logging.getLogger(__name__)


# =============================================================
# CONSTANTES
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

RE_RANGE = re.compile(
    rf"{RE_NUM}\s*[-/ ]\s*{RE_NUM}"
)


# =============================================================
# SPAM FILTER
# =============================================================
EXCLUDE_KEYWORDS = [
    "hit", "pips",
    "tp hit", "tp1 hit", "tp2 hit", "tp3 hit", "all tp hit",
    "mission acomplished", "boom boom boom",
    "my signal are on fire",
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

    for kw in EXCLUDE_KEYWORDS:
        if kw in low:
            return True

    for kw in SPAM_STANDALONE:
        for line in lines:
            stripped = line.strip().strip("📍🎯📊📈📉❌✅🔴🟢⚪")
            if stripped == kw or stripped == kw + ":":
                return True

    return False


# =============================================================
# HELPERS
# =============================================================
def _resolve_symbol(raw: str) -> str:
    clean = raw.upper().strip().replace(" ", "")
    return SYMBOL_MAP.get(clean, clean)


def _parse_range(text: str) -> tuple[float, float] | None:
    m = RE_RANGE.search(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def _extract_entry_price(text: str) -> float | None:
    """
    Extrait un prix d'entry unique (sans range) depuis le texte.
    Cherche les labels: ENTRY:, OPEN:, @, ZONE:, ou un prix après BUY/SELL.
    """
    # Pattern 1: ENTRY: 3240, ENTRY 3240, OPEN: 3240
    m = re.search(
        r"(?:ENTRY|OPEN|ENTER)\s*[:=]?\s*" + RE_NUM,
        text, re.IGNORECASE
    )
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 2: @ 3240, @3240
    m = re.search(r"@\s*" + RE_NUM, text)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 3: ZONE: 3240
    m = re.search(r"ZONE\s*[:=]?\s*" + RE_NUM, text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 4: BUY 3240 ou SELL 3240 (inline, sans range après)
    m = re.search(
        r"\b(BUY|SELL|LONG|SHORT)\s+" + RE_NUM + r"(?:\s|$|,|;|\n)",
        text, re.IGNORECASE
    )
    if m:
        val = float(m.group(2))
        if 1000 <= val <= 9999:
            return val

    return None


def _extract_tps(text: str) -> list[float]:
    """
    Extrait tous les TP d'un texte.
    10 patterns couvrant les formats courants des channels Telegram.
    """
    tps = {}

    # ── Pattern 1: TP1: 4628, TP2: 4631, TP.1: 3245 ──
    for m in re.finditer(
        r"TP[\.\s]*(\d+)\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    ):
        num = int(m.group(1))
        val = float(m.group(2))
        if 1000 <= val <= 9999:
            tps[num] = val

    # ── Pattern 2: TAKE PROFIT 1: 4655, TAKE PROFIT ONE 4650 ──
    if not tps:
        word_to_num = {
            "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
            "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
        }
        for m in re.finditer(
            r"TAKE\s*PROFIT\s*(\d+|ONE|TWO|THREE|FOUR|FIVE)?\s*(?:\(.*?\))?\s*[.:]?\s*"
            + RE_NUM,
            text, re.IGNORECASE
        ):
            num_str = m.group(1)
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                if num_str:
                    num = word_to_num.get(num_str.upper(), int(num_str) if num_str.isdigit() else len(tps) + 1)
                else:
                    num = len(tps) + 1
                tps[num] = val

    # ── Pattern 3: ✅ TP1: 4628, ☑️ TP 4633, ✅TP.⁴ 4688 ──
    if not tps:
        for m in re.finditer(
            r"[✅☑️✔️🎯]\s*TP[\.\s]*([\d⁰¹²³⁴⁵⁶⁷⁸⁹]+)?\s*[:\s]*\(?(\d+\.?\d*)\)?",
            text, re.IGNORECASE
        ):
            if m.group(1):
                sup_map = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
                           '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}
                num_str = ''.join(sup_map.get(ch, ch) for ch in m.group(1))
                num = int(num_str) if num_str.isdigit() else len(tps) + 1
            else:
                num = len(tps) + 1
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                tps[num] = val

    # ── Pattern 4: TP¹ 4633, TP² 4636, TPⁿ 4650 (superscript) ──
    if not tps:
        sup_map = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
                    '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9', 'ⁿ': ''}
        for m in re.finditer(
            r"TP[\s.]*([¹²³⁴⁵⁶⁷⁸⁹⁰ⁿ]+)\s*" + RE_NUM,
            text, re.IGNORECASE
        ):
            sup_str = m.group(1)
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                ascii_num = ''.join(sup_map.get(ch, ch) for ch in sup_str)
                num = int(ascii_num) if ascii_num else len(tps) + 1
                tps[num] = val

    # ── Pattern 5: TARGET 1: 4628, TGT 1 4628 ──
    if not tps:
        for m in re.finditer(
            r"(?:TARGET|TGT)\s*(\d+)?\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
            text, re.IGNORECASE
        ):
            num = int(m.group(1)) if m.group(1) else len(tps) + 1
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                tps[num] = val

    # ── Pattern 6: TP 4626 (ligne seule — sans numéro) ──
    if not tps:
        for m in re.finditer(
            r"^\s*TP\s+" + RE_NUM + r"(?:\s*[✅☑️✔️🎯]|\s+CONFIRM|\s+HIT)?\s*$",
            text, re.IGNORECASE | re.MULTILINE
        ):
            val = float(m.group(1))
            if 1000 <= val <= 9999:
                tps[len(tps) + 1] = val

    if not tps:
        return []

    return [tps[k] for k in sorted(tps.keys())]


def _extract_sl(text: str) -> float | None:
    """
    Extrait le SL d'un texte.
    10 patterns couvrant les formats courants.
    """
    # Pattern 1: Stop Loss (SL): 4605, STOP LOSS. 4670, STOP LOSS: (4619)
    m = re.search(
        r"(?:STOP\s*LOSS|Stop\s+Loss)\s*(?:\(\s*SL\s*\))?\s*[.:]?\s*\(?"
        + RE_NUM + r"\)?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    # Pattern 2: SL BREAKOUT 4650
    m = re.search(
        r"SL\s+BREAKOUT\s*[:\s.]*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    # Pattern 3: SL: 4615, SL_4646, SL_ 4646, SL-4650, SL. 4650
    m = re.search(
        r"SL\s*[_:\-.\s]+\s*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    # Pattern 4: (SL): 4615, (SL) 4615
    m = re.search(
        r"\(\s*SL\s*\)\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    # Pattern 5: STOP: 4650, STOP 4650
    m = re.search(
        r"\bSTOP\b\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    )
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 6: 🛑 SL 4650
    m = re.search(
        r"[🛑🔴]\s*(?:SL|STOP)\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    # Pattern 7: SL sans séparateur mais suivi d'un nombre à 4 chiffres
    m = re.search(
        r"\bSL\s+(\d{4}(?:\.\d+)?)\b",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    return None


def _extract_symbol(text: str) -> str | None:
    m = RE_SYMBOL.search(text)
    if m:
        return _resolve_symbol(m.group(1))
    return None


def _extract_action(text: str) -> str | None:
    m = RE_ACTION.search(text)
    if m:
        return m.group(1).upper()
    return None


def _detect_action_from_tps(zone_low: float, zone_high: float, tps: list[float]) -> str:
    avg_entry = (zone_low + zone_high) / 2
    avg_tp = sum(tps) / len(tps)
    return "BUY" if avg_tp > avg_entry else "SELL"


# =============================================================
# PARSER PRINCIPAL
# =============================================================
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

    # ---------------------------------------------------------
    # CLOSE
    # ---------------------------------------------------------
    def _parse_close(self, text: str) -> dict | None:
        m = re.search(r"close\s+(all|[A-Z]{3,10})", text, re.IGNORECASE)
        if not m:
            return None
        target = m.group(1).upper()
        return {
            "type": "CLOSE",
            "symbol": None if target == "ALL" else _resolve_symbol(target),
            "close_all": target == "ALL",
        }

    # ---------------------------------------------------------
    # SL MOVE
    # ---------------------------------------------------------
    def _parse_sl_move(self, text: str) -> dict | None:
        m = re.search(
            r"(?:SL\s*MOVE|MOVE\s*SL|New\s*SL|SL\s*→|SL\s*moved?\s*to)"
            r"\s*[:\s]*\s*" + RE_NUM,
            text, re.IGNORECASE
        )
        if m:
            return {
                "type": "SL_MOVE",
                "new_sl": float(m.group(1)),
            }
        return None

    # ---------------------------------------------------------
    # TRADE
    # ---------------------------------------------------------
    def _parse_trade(self, text: str) -> dict | None:
        symbol = _extract_symbol(text)
        action = _extract_action(text)
        tps = _extract_tps(text)
        sl = _extract_sl(text)

        if not symbol or not tps or sl is None:
            return None

        # ── Extraction entry : range d'abord, puis prix unique ──
        zone = _parse_range(text)
        if zone:
            zone_low, zone_high = zone
        else:
            # Fallback : prix unique labelé (ENTRY:, @, BUY 3240, etc.)
            entry_price = _extract_entry_price(text)
            if entry_price is not None:
                zone_low = entry_price
                zone_high = entry_price
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
            "type": "TRADE",
            "symbol": symbol,
            "action": action,
            "zone_low": zone_low,
            "zone_mid": zone_mid,
            "zone_high": zone_high,
            "tps": tps,
            "tp1": tps[0],
            "tp_final": tps[-1],
            "sl": sl,
        }

    @staticmethod
    def _validate_sl(action: str, entry_price: float, sl: float) -> bool:
        if action == "BUY" and sl >= entry_price:
            return False
        if action == "SELL" and sl <= entry_price:
            return False
        return True
