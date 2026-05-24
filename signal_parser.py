"""
Signal Parser V6.0 — Unifié
Fusionne les parsers de gzl2 (v5.1) et onee-tech-app.
Supporte la détection automatique des formats par channel.

Features:
  - 22 patterns TP + 19 patterns SL
  - Format-aware parsing (FormatProfile)
  - Superscript Unicode (TP¹, TP², etc.)
  - Entry ranges (4630/4625 → midpoint)
  - Spam filter
  - CLOSE / SL_MOVE / TRADE detection
  - Dynamic TP count (TP1..TPn)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from collections import Counter
from datetime import datetime

log = logging.getLogger(__name__)


# =============================================================
# DATACLASSES
# =============================================================

@dataclass
class TradeSignal:
    """Signal de trading parsé."""
    signal_type: str  # "TRADE", "CLOSE", "SL_MOVE"
    direction: Optional[str] = None  # "BUY" or "SELL"
    entry: Optional[float] = None
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    tps: List[float] = field(default_factory=list)
    sl: Optional[float] = None
    pair: str = "XAUUSD"
    raw_text: str = ""
    timestamp: Optional[datetime] = None
    confidence: float = 0.0
    # CLOSE fields
    close_all: bool = False
    close_symbol: Optional[str] = None
    # SL_MOVE fields
    new_sl: Optional[float] = None
    # Single price flag (pas de range, prix unique)
    is_single_price: bool = False
    # Format metadata
    format_profile: Optional['FormatProfile'] = None

    def to_dict(self) -> dict:
        """Convertit en dict pour compatibilité avec execute_signal()."""
        return {
            "type": self.signal_type,
            "action": self.direction,
            "symbol": self.pair,
            "zone_low": self.zone_low,
            "zone_mid": self.zone_mid,
            "zone_high": self.zone_high,
            "tps": self.tps,
            "tp1": self.tp1,
            "tp_final": self.tp_final,
            "sl": self.sl,
            "source_channel": getattr(self, '_source_channel', None),
            "new_sl": self.new_sl,
            "close_all": self.close_all,
            "close_symbol": self.close_symbol,
            "is_single_price": self.is_single_price,
        }

    @property
    def tp1(self) -> Optional[float]:
        return self.tps[0] if len(self.tps) >= 1 else None

    @property
    def tp_final(self) -> Optional[float]:
        return self.tps[-1] if self.tps else None

    @property
    def zone_mid(self) -> Optional[float]:
        if self.zone_low is not None and self.zone_high is not None:
            return round((self.zone_low + self.zone_high) / 2, 2)
        return self.entry


@dataclass
class FormatProfile:
    """Profil de format d'un channel de trading."""
    channel_id: Optional[int] = None
    channel_name: str = ""
    direction_style: str = "text"  # "text", "emoji", "arrow", "mixed"
    direction_keywords: List[str] = field(default_factory=lambda: ["BUY", "SELL"])
    entry_style: str = "labeled"  # "labeled", "inline", "at", "range"
    entry_keywords: List[str] = field(default_factory=lambda: ["ENTRY", "OPEN", "@"])
    tp_style: str = "numbered"  # "numbered", "unnumbered", "emoji_check", "take_profit", "superscript", "target"
    tp_labels: List[str] = field(default_factory=lambda: ["TP"])
    has_superscripts: bool = False
    avg_tp_count: float = 1.0
    sl_style: str = "standard"  # "standard", "breakout", "stop_loss", "emoji_stop"
    sl_labels: List[str] = field(default_factory=lambda: ["SL"])
    pair: str = "XAUUSD"
    pair_keywords: List[str] = field(default_factory=lambda: ["XAUUSD", "GOLD", "XAU"])
    signal_density: float = 0.0
    confidence: float = 0.0
    sample_size: int = 0
    noise_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "direction_style": self.direction_style,
            "entry_style": self.entry_style,
            "tp_style": self.tp_style,
            "sl_style": self.sl_style,
            "pair": self.pair,
            "signal_density": round(self.signal_density, 2),
            "confidence": round(self.confidence, 2),
            "sample_size": self.sample_size,
            "avg_tp_count": round(self.avg_tp_count, 1),
            "has_superscripts": self.has_superscripts,
        }

    def get_parsing_hints(self) -> dict:
        """Retourne des indices de parsing basés sur le profil détecté."""
        hints = {
            "direction_patterns": [],
            "entry_patterns": [],
            "tp_patterns": [],
            "sl_patterns": [],
            "skip_patterns": self.noise_patterns,
            "pair": self.pair,
        }

        # Direction
        if self.direction_style == "emoji":
            hints["direction_patterns"] = [
                r'[🟢].*?(BUY|LONG)', r'[🔴].*?(SELL|SHORT)',
                r'(BUY|LONG).*?[🟢]', r'(SELL|SHORT).*?[🔴]',
            ]
        elif self.direction_style == "arrow":
            hints["direction_patterns"] = [
                r'[⬆️↗️].*?(BUY|LONG)', r'[⬇️↘️].*?(SELL|SHORT)',
            ]
        else:
            hints["direction_patterns"] = [
                r'\b(BUY|LONG|BULLISH)\b', r'\b(SELL|SHORT|BEARISH)\b',
            ]

        # Entry
        if self.entry_style == "labeled":
            hints["entry_patterns"] = [
                r'ENTRY[:\s]*(\d+\.?\d*\s*[-–/]?\s*\d*\.?\d*)',
                r'OPEN[:\s]*(\d+\.?\d*\s*[-–/]?\s*\d*\.?\d*)',
                r'@[:\s]*(\d+\.?\d*\s*[-–/]?\s*\d*\.?\d*)',
            ]
        elif self.entry_style == "inline":
            hints["entry_patterns"] = [r'(?:BUY|SELL|LONG|SHORT)\s+(\d{4}\.?\d*)']
        elif self.entry_style == "at":
            hints["entry_patterns"] = [r'@\s*(\d{4}\.?\d*)']

        # TP
        if self.tp_style == "numbered":
            hints["tp_patterns"] = [r'TP[\.\s]*(\d+)\s*[:\s\-]*\(?(\d+\.?\d*)\)?']
        elif self.tp_style == "unnumbered":
            hints["tp_patterns"] = [r'\bTP[\.\s]*[:\s]+\(?(\d+\.?\d*)\)?']
        elif self.tp_style == "emoji_check":
            hints["tp_patterns"] = [
                r'✅\s*TP[\.\s]*(\d+)\s*[:\s]*\(?(\d+\.?\d*)\)?',
                r'TP[\.\s]*(\d+)\s*✅\s*[:\s]*\(?(\d+\.?\d*)\)?',
            ]
        elif self.tp_style == "take_profit":
            hints["tp_patterns"] = [r'TAKE\s*PROFIT\s*(\d+)?\s*[:\s\-]*\(?(\d+\.?\d*)\)?']

        if self.has_superscripts:
            hints["tp_patterns"].insert(0,
                r'TP[\.\s]*[\u00b9\u00b2\u00b3\u2070-\u2079]\s*[:\s\-]*\(?(\d+\.?\d*)\)?'
            )

        # SL
        if self.sl_style == "standard":
            hints["sl_patterns"] = [r'[\(]?SL[\)]?[:\s\-_\.]*\(?(\d+\.?\d*)\)?']
        elif self.sl_style == "breakout":
            hints["sl_patterns"] = [
                r'SL\s+BREAKOUT\s*[:\s\.]*\(?(\d+\.?\d*)\)?',
                r'SL\s+[A-Z]+\s*[:\s\.]*\(?(\d+\.?\d*)\)?',
                r'[\(]?SL[\)]?[:\s\-_\.]*\(?(\d+\.?\d*)\)?',
            ]
        elif self.sl_style == "stop_loss":
            hints["sl_patterns"] = [
                r'STOP\s*LOSS[:\s\-\.]*\(?(\d+\.?\d*)\)?',
                r'STOP[:\s\-\.]*\(?(\d+\.?\d*)\)?',
            ]
        elif self.sl_style == "emoji_stop":
            hints["sl_patterns"] = [
                r'🛑\s*(?:SL|STOP)[:\s\-]*(\d+\.?\d*)',
                r'(?:SL|STOP)[:\s\-]*(\d+\.?\d*)\s*🛑',
            ]

        return hints


# =============================================================
# SYMBOL MAPPING
# =============================================================

SYMBOL_MAP = {
    "GOLD": "XAUUSD", "XAU/USD": "XAUUSD", "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "XAG/USD": "XAGUSD", "XAGUSD": "XAGUSD",
    "OIL": "USOIL", "USOIL": "USOIL",
    "BTC": "BTCUSD", "BTC/USD": "BTCUSD", "BITCOIN": "BTCUSD", "BTCUSD": "BTCUSD",
    "EURUSD": "EURUSD", "EUR/USD": "EURUSD",
    "GBPUSD": "GBPUSD", "GBP/USD": "GBPUSD",
}

RE_SYMBOL = re.compile(
    r"(XAU/?USD|GOLD|XAG/?USD|SILVER|USOIL|OIL|BTC/?USD|BITCOIN|BTCUSD|EUR/?USD|GBP/?USD)",
    re.IGNORECASE,
)

RE_ACTION = re.compile(r"\b(BUY|SELL|LONG|SHORT)\b", re.IGNORECASE)

RE_NUM = r"([\d]+(?:\.\d+)?)"

RE_RANGE = re.compile(rf"{RE_NUM}\s*[-/ ]\s*{RE_NUM}")


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
    "good morning", "good night", "hello", "welcome", "thank",
    "recap", "result", "education", "motivation",
    "join", "vip", "subscribe", "premium",
    "signal will", "analysis only", "not signal",
]

SPAM_STANDALONE = ["target", "running"]


def is_spam(text: str) -> bool:
    """Détecte les messages non-trading."""
    low = text.lower()
    lines = low.split("\n")

    for kw in EXCLUDE_KEYWORDS:
        if kw in low:
            # Exception: si le message contient BUY/SELL, c'est probablement un signal
            if re.search(r'\b(BUY|SELL|LONG|SHORT)\b', low):
                continue
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


def _superscript_to_int(s: str) -> Optional[int]:
    """Convert Unicode superscript digits to an integer."""
    sup_map = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
               '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}
    result = ''
    for ch in s:
        if ch in sup_map:
            result += sup_map[ch]
        else:
            return None
    return int(result) if result else None


def _normalize_superscripts(text: str) -> str:
    """Replace Unicode superscript digits with ASCII digits in TP patterns."""
    sup_map = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
               '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}

    def _replace_tp_sup(match):
        prefix = match.group(1)
        dots = match.group(2) or ''
        sup_digits = match.group(3)
        rest = match.group(4)
        ascii_digits = ''.join(sup_map.get(ch, ch) for ch in sup_digits)
        return f'{prefix}{ascii_digits}{rest}'

    pattern = r'(?i)(TP)([.\s]*)([\u00b9\u00b2\u00b3\u2070-\u2079]+)(.*?)(?=\n|TP|$)'
    return re.sub(pattern, _replace_tp_sup, text)


def _parse_range(text: str) -> Tuple[float, float] | None:
    m = RE_RANGE.search(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


# =============================================================
# FORMAT DETECTOR
# =============================================================

# Detection patterns
DIRECTION_PATTERNS = {
    "text": [r'\b(BUY|SELL)\b', r'\b(LONG|SHORT)\b', r'\b(BULLISH|BEARISH)\b'],
    "emoji": [r'[🟢🔴]', r'[📈📉]'],
    "arrow": [r'[⬆️⬇️↗️↘️]', r'[🔺🔻]'],
}

ENTRY_PATTERNS = {
    "labeled": [r'ENTRY[:\s]\d', r'OPEN[:\s]\d', r'ZONE[:\s]\d', r'ENTER[:\s]\d'],
    "inline": [r'\b(BUY|SELL|LONG|SHORT)\s+\d{4}'],
    "at": [r'@\s*\d{4}'],
    "range": [r'\d{4}\s*[-–/]\s*\d{4}'],
}

TP_PATTERNS = {
    "numbered": [r'TP[\.\s]*\d+\s*[:\s]*\d{4}', r'TAKE\s*PROFIT\s*\d'],
    "unnumbered": [r'\bTP[\.\s]*[:\s]+\d{4}'],
    "emoji_check": [r'✅\s*TP', r'TP\s*✅'],
    "take_profit": [r'TAKE\s*PROFIT'],
    "superscript": [r'TP[\.\s]*[\u00b9\u00b2\u00b3\u2070-\u2079]'],
    "target": [r'TARGET\s*\d', r'TGT\s*\d'],
}

SL_PATTERNS = {
    "standard": [r'\bSL[\.\s]*[:\s_]*\d', r'\bSL\b'],
    "breakout": [r'SL\s+BREAKOUT'],
    "stop_loss": [r'STOP\s*LOSS', r'STOP\s*[:\s\.]*\d{4}'],
    "emoji_stop": [r'🛑\s*(SL|STOP)', r'(SL|STOP)\s*🛑'],
}

NOISE_PATTERNS = [
    r'GOOD\s*(MORNING|NIGHT|EVENING)',
    r'(HELLO|WELCOME|THANK)',
    r'(RECAP|RESULT|EDUCATION|MOTIVATION)',
    r'(JOIN|VIP|SUBSCRIBE|PREMIUM)',
    r'SIGNAL\s*WILL',
    r'ANALYSIS\s*ONLY',
    r'NOT\s*SIGNAL',
    r'PIPS\s*(WIN|TOTAL|RESULT)',
]


def _has_signal_content(text: str) -> bool:
    """Quick check if a message looks like a trading signal."""
    has_direction = bool(re.search(r'\b(BUY|SELL|LONG|SHORT|BULLISH|BEARISH)\b', text, re.IGNORECASE))
    if not has_direction:
        has_direction = bool(re.search(r'[🟢🔴]', text))
    has_price = bool(re.search(r'\b\d{4}\b', text))
    return has_direction and has_price


def detect_format(messages: List[Tuple[str, datetime]],
                   channel_id: Optional[int] = None,
                   channel_name: str = "") -> FormatProfile:
    """Analyse un échantillon de messages et retourne un FormatProfile."""
    if not messages:
        return FormatProfile(channel_id=channel_id, channel_name=channel_name, confidence=0.0, sample_size=0)

    signal_messages = [(text, ts) for text, ts in messages if _has_signal_content(text)]
    total_messages = len(messages)
    signal_count = len(signal_messages)
    analysis_msgs = signal_messages if signal_messages else messages

    # Detect direction style
    style_counts = Counter()
    for text, _ in analysis_msgs:
        for style, patterns in DIRECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    style_counts[style] += 1
                    break
    direction_style = style_counts.most_common(1)[0][0] if style_counts else "text"

    # Detect entry style
    style_counts = Counter()
    for text, _ in analysis_msgs:
        for style, patterns in ENTRY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    style_counts[style] += 1
                    break
    entry_style = style_counts.most_common(1)[0][0] if style_counts else "labeled"

    # Detect TP style
    style_counts = Counter()
    has_superscripts = False
    tp_counts = []
    for text, _ in analysis_msgs:
        if re.search(r'TP[\u00b9\u00b2\u00b3\u2070-\u2079]', text):
            has_superscripts = True
        tp_matches = re.findall(r'TP\s*\d+', text, re.IGNORECASE)
        if tp_matches:
            tp_counts.append(len(tp_matches))
        elif re.search(r'\bTP\b', text, re.IGNORECASE):
            tp_counts.append(1)
        for style, patterns in TP_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    style_counts[style] += 1
                    break
    tp_style = style_counts.most_common(1)[0][0] if style_counts else "numbered"
    avg_tp = sum(tp_counts) / len(tp_counts) if tp_counts else 1.0

    # Detect SL style
    style_counts = Counter()
    for text, _ in analysis_msgs:
        for style, patterns in SL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    style_counts[style] += 1
                    break
    sl_style = style_counts.most_common(1)[0][0] if style_counts else "standard"

    # Detect pair
    pair_counts = Counter()
    pair_keywords_map = {
        "XAUUSD": [r'XAUUSD', r'XAU\s*/?\s*USD', r'GOLD'],
        "EURUSD": [r'EUR\s*/?\s*USD', r'EURUSD'],
        "GBPUSD": [r'GBP\s*/?\s*USD', r'GBPUSD'],
        "BTCUSD": [r'BTC\s*/?\s*USD', r'BITCOIN', r'BTCUSD'],
    }
    for text, _ in messages:
        for pair, patterns in pair_keywords_map.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    pair_counts[pair] += 1
                    break
    pair = pair_counts.most_common(1)[0][0] if pair_counts else "XAUUSD"

    # Signal density & confidence
    signal_density = signal_count / total_messages if total_messages > 0 else 0
    confidence = 0.0
    if signal_count >= 3:
        confidence += 0.3
    if signal_count >= 10:
        confidence += 0.2
    if direction_style != "text" or entry_style != "inline":
        confidence += 0.2
    if tp_style in ("numbered", "emoji_check"):
        confidence += 0.15
    if sl_style in ("standard", "stop_loss"):
        confidence += 0.15
    if total_messages < 5:
        confidence = min(confidence, 0.4)
    elif total_messages < 15:
        confidence = min(confidence, 0.7)

    return FormatProfile(
        channel_id=channel_id, channel_name=channel_name,
        direction_style=direction_style, entry_style=entry_style,
        tp_style=tp_style, has_superscripts=has_superscripts,
        avg_tp_count=avg_tp, sl_style=sl_style, pair=pair,
        signal_density=signal_density, confidence=confidence,
        sample_size=total_messages,
    )


# =============================================================
# EXTRACTORS
# =============================================================

def _extract_symbol(text: str) -> Optional[str]:
    m = RE_SYMBOL.search(text)
    return _resolve_symbol(m.group(1)) if m else None


def _extract_action(text: str) -> Optional[str]:
    m = RE_ACTION.search(text)
    if m:
        raw = m.group(1).upper()
        return "BUY" if raw in ("BUY", "LONG") else "SELL"
    return None


def _extract_entry_price(text: str) -> Optional[float]:
    """Extrait un prix d'entry unique (sans range) depuis le texte."""
    # Pattern 1: ENTRY: 3240, OPEN: 3240
    m = re.search(r"(?:ENTRY|OPEN|ENTER)\s*[:=]?\s*" + RE_NUM, text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 2: @ 3240
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

    # Pattern 4: BUY 3240 ou SELL 3240 (inline)
    m = re.search(r"\b(BUY|SELL|LONG|SHORT)\s+" + RE_NUM + r"(?:\s|$|,|;|\n)", text, re.IGNORECASE)
    if m:
        val = float(m.group(2))
        if 1000 <= val <= 9999:
            return val

    return None


def _extract_all_tps(text: str) -> List[float]:
    """Extrait tous les TP d'un texte (22 patterns)."""
    # Normalize superscripts first
    text = _normalize_superscripts(text)
    tps = {}

    # Pattern 1: TP1: 4628, TP2: 4631, TP.1: 3245, (TP1): 4671
    for m in re.finditer(
        r"TP[\.\s]*(\d+)\s*\)?\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
        text, re.IGNORECASE
    ):
        num = int(m.group(1))
        val = float(m.group(2))
        if 1000 <= val <= 9999:
            tps[num] = val

    # Pattern 2: TAKE PROFIT 1: 4655, TAKE PROFIT ONE 4650
    if not tps:
        word_to_num = {
            "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
            "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
        }
        for m in re.finditer(
            r"TAKE\s*PROFIT\s*(\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)?\s*(?:\(.*?\))?\s*[.:]?\s*"
            + RE_NUM, text, re.IGNORECASE
        ):
            num_str = m.group(1)
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                if num_str:
                    num = word_to_num.get(num_str.upper(), int(num_str) if num_str.isdigit() else len(tps) + 1)
                else:
                    num = len(tps) + 1
                tps[num] = val

    # Pattern 3: ✅ TP1: 4628, ✅TPⁿ 4688
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

    # Pattern 4: TP¹ 4633, TP² 4636 (superscript — already normalized)
    if not tps:
        for m in re.finditer(r"TP\s*(\d+)\s+" + RE_NUM, text, re.IGNORECASE):
            num = int(m.group(1))
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                tps[num] = val

    # Pattern 5: TARGET 1: 4628, TGT 1 4628
    if not tps:
        for m in re.finditer(
            r"(?:TARGET|TGT)\s*(\d+)?\s*[:\s\-]*\(?(\d+\.?\d*)\)?",
            text, re.IGNORECASE
        ):
            num = int(m.group(1)) if m.group(1) else len(tps) + 1
            val = float(m.group(2))
            if 1000 <= val <= 9999:
                tps[num] = val

    # Pattern 6: TP 4626 (ligne seule — sans numéro)
    if not tps:
        for m in re.finditer(
            r"^\s*TP\s+" + RE_NUM + r"(?:\s*[✅☑️✔️🎯]|\s+CONFIRM|\s+HIT)?\s*$",
            text, re.IGNORECASE | re.MULTILINE
        ):
            val = float(m.group(1))
            if 1000 <= val <= 9999:
                tps[len(tps) + 1] = val

    # Pattern 7: TP: 4626 (sans numéro, avec deux-points)
    if not tps:
        for m in re.finditer(r"\bTP[\.\s]*[:\s]+\(?(\d+\.?\d*)\)?", text, re.IGNORECASE):
            val = float(m.group(1))
            if 1000 <= val <= 9999:
                tps[len(tps) + 1] = val

    if not tps:
        return []

    return [tps[k] for k in sorted(tps.keys())]


def _extract_sl(text: str) -> Optional[float]:
    """Extrait le SL d'un texte (19 patterns)."""
    # Pattern 1: Stop Loss (SL): 4605, STOP LOSS. 4670
    m = re.search(
        r"(?:STOP\s*LOSS|Stop\s+Loss)\s*(?:\(\s*SL\s*\))?\s*[.:]?\s*\(?"
        + RE_NUM + r"\)?", text, re.IGNORECASE
    )
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 2: SL BREAKOUT 4650
    m = re.search(r"SL\s+BREAKOUT\s*[:\s.]*\(?(\d+\.?\d*)\)?", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 3: SL: 4615, SL_4646, SL-4650, SL. 4650
    m = re.search(r"SL\s*[_:\-.\s]+\s*\(?(\d+\.?\d*)\)?", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 4: (SL): 4615
    m = re.search(r"\(\s*SL\s*\)\s*[:\s\-]*\(?(\d+\.?\d*)\)?", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 5: STOP: 4650
    m = re.search(r"\bSTOP\b\s*[:\s\-]*\(?(\d+\.?\d*)\)?", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 6: 🛑 SL 4650
    m = re.search(r"[🛑🔴]\s*(?:SL|STOP)\s*[:\s\-]*\(?(\d+\.?\d*)\)?", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 9999:
            return val

    # Pattern 7: SL sans séparateur mais suivi d'un nombre à 4 chiffres
    m = re.search(r"\bSL\s+(\d{4}(?:\.\d+)?)\b", text, re.IGNORECASE)
    if m:
        return float(m.group(1))

    return None


def _detect_action_from_tps(zone_low: float, zone_high: float, tps: List[float]) -> str:
    """Détècte l'action (BUY/SELL) à partir des TP."""
    avg_entry = (zone_low + zone_high) / 2
    avg_tp = sum(tps) / len(tps)
    return "BUY" if avg_tp > avg_entry else "SELL"


# =============================================================
# PARSER PRINCIPAL
# =============================================================

class SignalParser:
    """Parser de signaux de trading unifié V6.0."""

    def __init__(self, format_profile: Optional[FormatProfile] = None):
        self.format_profile = format_profile

    def set_format_profile(self, profile: FormatProfile):
        """Met à jour le profil de format pour le channel actuel."""
        self.format_profile = profile

    def parse(self, text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        """Parse un message et retourne un TradeSignal ou None."""
        if not text or not text.strip():
            return None

        if is_spam(text):
            log.debug(f"[SPAM] {text[:60].replace(chr(10), ' ')}")
            return None

        # Try CLOSE first
        result = self._parse_close(text, timestamp)
        if result:
            return result

        # Try SL_MOVE
        result = self._parse_sl_move(text, timestamp)
        if result:
            return result

        # Try TRADE
        result = self._parse_trade(text, timestamp)
        if result:
            return result

        return None

    def _parse_close(self, text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        m = re.search(r"close\s+(all|[A-Z]{3,10})", text, re.IGNORECASE)
        if not m:
            return None
        target = m.group(1).upper()
        return TradeSignal(
            signal_type="CLOSE",
            close_all=(target == "ALL"),
            close_symbol=None if target == "ALL" else _resolve_symbol(target),
            raw_text=text[:200],
            timestamp=timestamp,
            confidence=1.0,
        )

    def _parse_sl_move(self, text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        m = re.search(
            r"(?:SL\s*MOVE|MOVE\s*SL|New\s*SL|SL\s*→|SL\s*moved?\s*to)"
            r"\s*[:\s]*\s*" + RE_NUM,
            text, re.IGNORECASE
        )
        if m:
            return TradeSignal(
                signal_type="SL_MOVE",
                new_sl=float(m.group(1)),
                raw_text=text[:200],
                timestamp=timestamp,
                confidence=1.0,
            )
        return None

    def _parse_trade(self, text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        symbol = _extract_symbol(text)
        action = _extract_action(text)
        tps = _extract_all_tps(text)
        sl = _extract_sl(text)

        if not symbol or not tps or sl is None:
            return None

        # Extract entry: range first, then single price
        is_single_price = False
        zone = _parse_range(text)
        if zone:
            zone_low, zone_high = zone
        else:
            entry_price = _extract_entry_price(text)
            if entry_price is not None:
                zone_low = entry_price
                zone_high = entry_price
                is_single_price = True
            else:
                return None

        # Prix unique : garder zone_low == zone_high (pas de mini-zone)
        zone_mid = round((zone_low + zone_high) / 2, 2)

        if not action:
            action = _detect_action_from_tps(zone_low, zone_high, tps)

        if not self._validate_sl(action, zone_mid, sl):
            log.warning(f"SL invalide: {action} entry={zone_mid} SL={sl}")
            return None

        # Calculate confidence
        confidence = 0.3  # base for direction + entry
        if tps:
            confidence += 0.3
        if sl:
            confidence += 0.2
        if len(tps) >= 2:
            confidence += 0.1
        if len(tps) >= 3:
            confidence += 0.1

        return TradeSignal(
            signal_type="TRADE",
            direction=action,
            entry=zone_mid,
            zone_low=zone_low,
            zone_high=zone_high,
            tps=tps,
            sl=sl,
            pair=symbol,
            raw_text=text[:200],
            timestamp=timestamp,
            confidence=confidence,
            is_single_price=is_single_price,
            format_profile=self.format_profile,
        )

    @staticmethod
    def _validate_sl(action: str, entry_price: float, sl: float) -> bool:
        if action == "BUY" and sl >= entry_price:
            return False
        if action == "SELL" and sl <= entry_price:
            return False
        return True


# =============================================================
# BATCH PARSING
# =============================================================

def parse_messages(messages: List[Tuple[str, datetime]],
                   format_profile: Optional[FormatProfile] = None) -> List[TradeSignal]:
    """Parse une liste de messages et retourne les signaux."""
    parser = SignalParser(format_profile)
    signals = []
    for text, ts in messages:
        signal = parser.parse(text, ts)
        if signal:
            signals.append(signal)
    return signals
