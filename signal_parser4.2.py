"""
Signal Parser V5 — Reconstruit de zéro
Supporte les formats réels des canaux Telegram trading.

Formats supportés:
  1. DAILY SIGNAL: "XAUUSD DAILY SIGNAL" + Pair/Action/Entry/TP/SL structurés
  2. SELL NOW: "XAUUSD Sell NOW 4659/4662" + TAKE PROFIT / STOP LOSS
  3. BUY zone: "XAUUSD (GOLD) BUY 4630-4625" + TPⁿ / SL
  4. SMC: "💎XAU/USD SELL 4630/4638" + TP¹²³ / SL_
  5. SL_MOVE: "SL MOVE 4650", "New SL: 4650", etc.
  6. CLOSE: "close all", "close XAUUSD"
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

# Regex pour capturer le symbole dans un texte
RE_SYMBOL = re.compile(
    r"(XAU/?USD|GOLD|XAG/?USD|SILVER|USOIL|OIL|BTC/?USD|BITCOIN|BTCUSD)",
    re.IGNORECASE,
)

# Regex pour BUY/SELL
RE_ACTION = re.compile(r"\b(BUY|SELL)\b", re.IGNORECASE)

# Regex pour un nombre décimal
RE_NUM = r"([\d]+(?:\.\d+)?)"

# Regex pour un range de prix: 4625/4620 ou 4630-4625 ou 4630 4624
RE_RANGE = re.compile(
    rf"{RE_NUM}\s*[-/ ]\s*{RE_NUM}"
)


# =============================================================
# SPAM FILTER
# =============================================================
EXCLUDE_KEYWORDS = [
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
    """Convertit un symbole brut en symbole standard."""
    clean = raw.upper().strip().replace(" ", "")
    return SYMBOL_MAP.get(clean, clean)


def _parse_range(text: str) -> tuple[float, float] | None:
    """Extrait un range de prix (low, high) d'un texte."""
    m = RE_RANGE.search(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def _extract_tps(text: str) -> list[float]:
    """
    Extrait tous les TP d'un texte.
    Gère les formats:
      - ✅ TP1: 4628
      - 🥳TAKE PROFIT. 4655
      - ☑️ TP¹ 4633
      - TP¹ 4626
      - TPⁿ 4650 Open 4670
      - TP 4626 (ligne seule)
    """
    tps = []

    # Pattern 1: TP1: 4628, TP2: 4631, etc.
    for m in re.finditer(r"TP\s*\d+\s*[:.]\s*" + RE_NUM, text, re.IGNORECASE):
        tps.append(float(m.group(1)))

    if tps:
        return tps

    # Pattern 2: TAKE PROFIT. 4655, TAKE PROFIT 4650 CONFIRM
    for m in re.finditer(r"TAKE\s+PROFIT\s*[.:]?\s*" + RE_NUM, text, re.IGNORECASE):
        tps.append(float(m.group(1)))

    if tps:
        return tps

    # Pattern 3: TP 4633, TP 4636 (lignes seules — TP suivi d'un nombre, avec optionnel emoji/texte)
    for m in re.finditer(
        r"^\s*TP\s+" + RE_NUM + r"(?:\s*[✅☑️✔️🎯]|\s+CONFIRM|\s+HIT)?\s*$",
        text, re.IGNORECASE | re.MULTILINE
    ):
        tps.append(float(m.group(1)))

    if tps:
        return tps

    # Pattern 4: TP¹ 4633, TP² 4636, TPⁿ 4650 (superscript)
    for m in re.finditer(
        r"TP\s*[¹²³⁴⁵⁶⁷⁸⁹⁰ⁿ]\s*" + RE_NUM,
        text, re.IGNORECASE
    ):
        tps.append(float(m.group(1)))

    return tps


def _extract_sl(text: str) -> float | None:
    """
    Extrait le SL d'un texte.
    Gère les formats:
      - ❌ Stop Loss (SL): 4605
      - ❌STOP LOSS. 4670
      - ❌ SL : 4615
      - SL_ 4646
      - SL 4646 (ligne seule)
    """
    # Pattern 1: Stop Loss (SL): 4605 ou STOP LOSS. 4670
    m = re.search(
        r"(?:STOP\s*LOSS|Stop\s+Loss)\s*(?:\(\s*SL\s*\))?\s*[.:]?\s*" + RE_NUM,
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    # Pattern 2: SL : 4615, SL_ 4646, SL: 4615
    m = re.search(
        r"SL\s*[_:.]?\s*" + RE_NUM,
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1))

    return None


def _extract_symbol(text: str) -> str | None:
    """Extrait le symbole d'un texte."""
    m = RE_SYMBOL.search(text)
    if m:
        return _resolve_symbol(m.group(1))
    return None


def _extract_action(text: str) -> str | None:
    """Extrait BUY ou SELL d'un texte."""
    m = RE_ACTION.search(text)
    if m:
        return m.group(1).upper()
    return None


def _detect_action_from_tps(zone_low: float, zone_high: float, tps: list[float]) -> str:
    """Déduit BUY/SELL en comparant les TPs à la zone d'entrée."""
    avg_entry = (zone_low + zone_high) / 2
    avg_tp = sum(tps) / len(tps)
    return "BUY" if avg_tp > avg_entry else "SELL"


# =============================================================
# PARSER PRINCIPAL
# =============================================================
class SignalParser:
    """
    Parseur de signaux de trading.
    Retourne un dict normalisé ou None si le message n'est pas un signal.
    """

    def parse(self, text: str) -> dict | None:
        """
        Point d'entrée principal.
        Retourne:
          {"type": "TRADE", "symbol": ..., "action": ..., "zone_low": ...,
           "zone_mid": ..., "zone_high": ..., "tps": [...], "sl": ...}
          {"type": "SL_MOVE", "new_sl": ...}
          {"type": "CLOSE", "symbol": ..., "close_all": ...}
          None (spam ou non reconnu)
        """
        if not text or not text.strip():
            return None

        # Spam check en premier
        if is_spam(text):
            log.debug(f"[SPAM] {text[:60].replace(chr(10), ' ')}")
            return None

        # Essayer chaque parser dans l'ordre
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
    # TRADE (tous formats)
    # ---------------------------------------------------------
    def _parse_trade(self, text: str) -> dict | None:
        # Extraire les composants
        symbol = _extract_symbol(text)
        action = _extract_action(text)
        tps = _extract_tps(text)
        sl = _extract_sl(text)

        # Extraire la zone d'entrée
        zone = _parse_range(text)

        if not symbol or not tps or sl is None:
            return None

        if zone:
            zone_low, zone_high = zone
        else:
            # Pas de range trouvé — essayer de trouver un prix unique
            # (ex: "Entry 4630" sans range)
            return None

        if zone_low == zone_high:
            # Un seul prix → créer une mini-zone autour
            zone_high = zone_low + 0.5
            zone_low = zone_low - 0.5

        zone_mid = round((zone_low + zone_high) / 2, 2)

        # Déduire l'action si non trouvée
        if not action:
            action = _detect_action_from_tps(zone_low, zone_high, tps)

        # Valider le SL
        if not self._validate_sl(action, zone_mid, sl):
            log.warning(
                f"SL invalide: {action} entry={zone_mid} SL={sl}"
            )
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
        """Vérifie que le SL est du bon côté par rapport à l'entrée."""
        if action == "BUY" and sl >= entry_price:
            return False
        if action == "SELL" and sl <= entry_price:
            return False
        return True
