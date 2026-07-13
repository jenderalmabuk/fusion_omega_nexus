"""
Parser for free-text trade-call signals from Telegram groups / Discord.

Handles the common "crypto signal" layout, tolerant to emojis, bold markdown,
arrows, and varied labels. Returns a ParsedSignal or None if the text does not
look like an actionable trade call.

Design goals:
- Be strict enough to avoid false positives (chatter, news, ads).
- Be lenient on formatting (emoji, **bold**, →, -, :, multiple TPs).
- Never raise on bad input; return None instead.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .signal_schema import ParsedSignal, SignalSide, SignalSource

# --- known quote assets, longest first so matching is greedy-correct ---
_QUOTES = ("USDT", "USDC", "BUSD", "USD", "PERP")

# Pair like "ZEC/USDT", "ZECUSDT", "BTC-USDT", "$ZEC"
_PAIR_RE = re.compile(
    r"(?:pair|coin|symbol|ticker)\s*[:\-]?\s*"
    r"[*_`]*\$?([A-Z0-9]{2,15})\s*[\/\-]?\s*(USDT|USDC|BUSD|USD)?[*_`]*",
    re.IGNORECASE,
)

# Fallback pair detection anywhere: "ZEC/USDT", "$ZEC", or bare "ZECUSDT"
_PAIR_INLINE_RE = re.compile(
    r"\$?\b([A-Z0-9]{2,15})\s*[\/\-]\s*(USDT|USDC|BUSD|USD)\b",
    re.IGNORECASE,
)
# Concatenated form: "ZECUSDT", "BTCUSDT PERPETUAL"
_PAIR_CONCAT_RE = re.compile(
    r"\$?\b([A-Z0-9]{2,12}?)(USDT|USDC|BUSD)\b",
    re.IGNORECASE,
)
# Cashtag form: "$ZETA", "$BEAT", "$MOCA" (no quote suffix) -> append USDT
_PAIR_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,14})\b")

_SIDE_RE = re.compile(
    r"(?:position|side|direction|type|signal|setup(?:\s*utama)?)\s*[:\-]?\s*[*_`(]*\s*"
    r"(LONG|SHORT|BUY|SELL)\b",
    re.IGNORECASE,
)
_SIDE_INLINE_RE = re.compile(r"\b(LONG|SHORT)\b", re.IGNORECASE)

_LEVERAGE_RE = re.compile(
    r"(?:leverage|lev|cross|isolated)\s*[:\-]?\s*[*_`]*\s*(\d{1,3}(?:\.\d+)?)\s*[xX]?",
    re.IGNORECASE,
)
_LEVERAGE_INLINE_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*[xX]\b")

# Entry zone: "Entry: 358 - 350", "Entry Zone:\n• 358-350", "ENTRY AREA (LONG) 376.0 - 378.5"
# The filler between the label and the price must NOT cross a competing label
# (Stop Loss / Target / TP / Take Profit / Risk) — otherwise "Entry Long Now
# Stop Loss : 0.4154" would grab the SL value as the entry.
_ENTRY_FILLER = (r"(?:(?!\b(?:stop\s*loss|stoploss|stop|sl|target|targets|tp|"
                 r"take\s*profit|take|profit|risk|lev|leverage)\b)[^\d]){0,40}?")
_ENTRY_RE = re.compile(
    r"(?:entry(?:\s*zone)?(?:\s*area)?|entry\s*price|buy\s*zone|enter)\s*[:\-@]?"
    + _ENTRY_FILLER + r"\$?([\d.,]+)\s*(?:[-–~]+|\bto\b)\s*\$?([\d.,]+)"
    r"|(?:entry(?:\s*zone)?(?:\s*area)?|entry\s*price|buy\s*zone|enter)\s*[:\-@]?"
    + _ENTRY_FILLER + r"\$?([\d.,]+)",
    re.IGNORECASE,
)
# Enumerated entry zone, common in Telegram channels:
# "Entry Price: 1) 1.963 2) 1.904".
_ENTRY_ENUM_RE = re.compile(
    r"(?:entry(?:\s*zone)?(?:\s*area)?|entry\s*price|buy\s*zone|enter)\s*[:\-@]?"
    + _ENTRY_FILLER + r"(?:\d{1,2}\s*[\).]\s*)\$?([\d.,]+)\s+"
    r"(?:\d{1,2}\s*[\).]\s*)\$?([\d.,]+)",
    re.IGNORECASE,
)
# Hybrid market/zone form: "Entry: now/388-376".
_ENTRY_NOW_ZONE_RE = re.compile(
    r"\bentry\b[^\n\d]{0,24}?\b(?:now|market|cmp)\b\s*[\/|,]?\s*"
    r"\$?([\d.,]+)\s*(?:[-–~]+|\bto\b)\s*\$?([\d.,]+)",
    re.IGNORECASE,
)
# Numbered-list entry format like "Entry Targets:\n1) 2.24\n2) 2.30"
# This format has no prices on the same line as the label.
_ENTRY_LIST_LABEL_RE = re.compile(
    r"(?:entry\s*targets?|entry\s*zone|entry\s*points?)\s*:\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ENTRY_LIST_NUMBER_RE = re.compile(r"\b\d{1,2}\s*[.)]\s*\$?([\d.,]+)\b", re.IGNORECASE)
# Market entry with NO explicit price: "Entry Long now", "Entry market", "CMP".
_ENTRY_MARKET_RE = re.compile(
    r"\bentry\b[^\n\d]{0,18}?\b(now|market|market\s*price|cmp|sekarang)\b",
    re.IGNORECASE,
)

_SL_RE = re.compile(
    r"(?:stop\s*loss|stoploss|stop\s*target|stop|sl)\s*[:\\-–—]*\s*[\-–—]?\s*[*_`]*\$?([\d.,]+)(?!\s*[.)])",
    re.IGNORECASE,
)
# New: numbered-list SL format like "Stop Target:\n1) 2.37"
_SL_LIST_LABEL_RE = re.compile(
    r"(?:stop\s*target|stop\s*loss|sl)\s*:\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SL_LIST_NUMBER_RE = re.compile(r"\b\d{1,2}\s*[.)]\s*\$?([\d.,]+)\b", re.IGNORECASE)

# Take profits: capture the whole targets block then extract numbers.
_TP_BLOCK_RE = re.compile(
    r"(?:take\s*profits?|targets?|tps?)\b(.*?)(?:stop\s*loss|stoploss|\bstop\b|\bsl\b|⛔|$)",
    re.IGNORECASE | re.DOTALL,
)
# A line that carries a take-profit value, e.g. "TP1 365", "TP 1 → 365",
# "TP1: 386.0", "Target 2 - 415", "Take Profit 365".
_TP_LINE_RE = re.compile(r"(?:take\s*profits?|targets?|\btp)\s*(\d{1,2})?\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\$?([\d][\d.,]*\d|\d)")

# Strong indicator that text is a trade call (need at least entry+side+pair).
_SIGNAL_HINT_RE = re.compile(
    r"(entry|long|short|target|tp\d|stop\s*loss|leverage)",
    re.IGNORECASE,
)

# Messages that are NOT fresh entries (cancels, closes, quoted previews, results).
_NOISE_RE = re.compile(
    r"\b(cancel|cancell?ed|canceled|closed|close\s+position|stop(?:ped)?\s*out|"
    r"tp\s*hit|sl\s*hit|full\s*tp|all\s*tp|invalidat|batal|tutup\s*posisi|"
    r"book(?:ed)?\s*profit|target\s*hit|all\s*targets?\s*(?:hit|done)|"
    r"congr|terbang)\b",
    re.IGNORECASE,
)
# Truncated quote preview, e.g. '... Target : di ch..."'
_QUOTE_PREVIEW_RE = re.compile(r'\.\.\.\s*["”]|…\s*["”]')

# Entry type: limit vs market.
_LIMIT_RE = re.compile(
    r"\b(entry\s*limit|limit\s*entry|buy\s*limit|sell\s*limit)\b|"
    r"\blimit\b(?=.*\bentry\b)|\bentry\b(?=.*\blimit\b)",
    re.IGNORECASE | re.DOTALL,
)

# Timeframe: "Time frame : 1h", "TF: 30m", "30m", "1H", "4 hour", "15min"
_TIMEFRAME_LABEL_RE = re.compile(
    r"(?:time\s*frame|timeframe|\btf)\s*[:\-]?\s*(\d{1,2})\s*(m|min|mins|minute|h|hr|hour|d|day|w|week)\b",
    re.IGNORECASE,
)
_TIMEFRAME_INLINE_RE = re.compile(
    r"\b(\d{1,2})\s*(m|min|h|hr|d|w)\b",
    re.IGNORECASE,
)


def _normalize_tf(num: str, unit: str) -> Optional[str]:
    try:
        n = int(num)
    except (TypeError, ValueError):
        return None
    u = (unit or "").lower()
    if u.startswith("m"):  # m, min, mins, minute
        return f"{n}m"
    if u.startswith("h"):  # h, hr, hour
        return f"{n}h"
    if u.startswith("d"):
        return f"{n}d"
    if u.startswith("w"):
        return f"{n}w"
    return None


def _to_float(raw: str) -> Optional[float]:
    if raw is None:
        return None
    s = raw.strip().replace(" ", "")
    if not s:
        return None
    # Handle thousands separators vs decimal. Common cases:
    #  "1,234.56" -> 1234.56 ; "0,358" (rare) -> treat comma as decimal if no dot
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        # crypto signal channels commonly use a decimal comma ("0,2388" -> 0.2388,
        # "3,2" -> 3.2). A single comma = decimal; multiple commas = thousands.
        if s.count(",") == 1:
            left, right = s.split(",", 1)
            # But values like ETH "1,718" are thousands, not decimal. Treat a
            # single comma followed by exactly 3 digits as thousands unless the
            # integer part is explicitly zero ("0,2388" remains decimal).
            if left not in {"", "0"} and len(right) == 3 and right.isdigit():
                s = left + right
            else:
                s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


_BINANCE_THOUSAND = {
    "PEPEUSDT": "1000PEPEUSDT",
    "BONKUSDT": "1000BONKUSDT",
    "FLOKIUSDT": "1000FLOKIUSDT",
    "SHIBUSDT": "1000SHIBUSDT",
    "LUNCUSDT": "1000LUNCUSDT",
    "PEPE": "1000PEPEUSDT",
    "BONK": "1000BONKUSDT",
    "FLOKI": "1000FLOKIUSDT",
    "SHIB": "1000SHIBUSDT",
    "LUNC": "1000LUNCUSDT",
}

def _normalize_symbol(base: str, quote: Optional[str]) -> str:
    base = (base or "").upper().strip().lstrip("$")
    # Clean Bybit .P suffix (e.g. BSBUSDT.P -> BSBUSDT)
    if base.endswith(".P"):
        base = base[:-2]
    quote = (quote or "USDT").upper().strip()
    if not base:
        return ""
    # If base already ends with a quote (e.g. "ZECUSDT"), keep as-is.
    for q in _QUOTES:
        if base.endswith(q) and base != q:
            result = base
            # Normalize Binance thousand-lot pairs (PEPEUSDT → 1000PEPEUSDT)
            result = _BINANCE_THOUSAND.get(result, result)
            return result
    if quote == "PERP" or quote == "USD":
        quote = "USDT"
    result = f"{base}{quote}"
    result = _BINANCE_THOUSAND.get(result, result)
    return result


def _normalize_side(token: str) -> Optional[SignalSide]:
    t = (token or "").upper().strip()
    if t in ("LONG", "BUY"):
        return SignalSide.LONG
    if t in ("SHORT", "SELL"):
        return SignalSide.SHORT
    return None


def _extract_take_profits(text: str) -> List[float]:
    tps: List[float] = []

    # Line-based: each TP line contributes its price(s); drop a leading index
    # number that comes from the label itself (e.g. the "1" in "TP1 365").
    for line in text.splitlines():
        m = _TP_LINE_RE.search(line)
        if not m:
            continue
        line_for_numbers = re.sub(r"\b\d{1,2}\s*\)\s*(?=\d)", "", line)
        line_for_numbers = re.sub(
            r"\bTP\s*\d{1,2}\b\s*[:\-–—]*", "", line_for_numbers, flags=re.IGNORECASE
        )
        nums = []
        for nm in _NUMBER_RE.finditer(line_for_numbers):
            v = _to_float(nm.group(1))
            if v is not None:
                nums.append(v)
        if not nums:
            continue
        if m.group(1):  # label had an index like TP1 / TP 1
            idx = _to_float(m.group(1))
            if idx is not None and nums and abs(nums[0] - idx) < 1e-9:
                nums = nums[1:]
        tps.extend(nums)
    if tps:
        return tps

    # Fallback: numbers inside the targets block.
    block = _TP_BLOCK_RE.search(text)
    if block:
        block_text = re.sub(r"\b\d{1,2}\s*\)\s*(?=\d)", "", block.group(1))
        block_text = re.sub(r"\bTP\s*\d{1,2}\b\s*[:\-–—]*", "", block_text, flags=re.IGNORECASE)
        for m in _NUMBER_RE.finditer(block_text):
            v = _to_float(m.group(1))
            if v is not None and v > 0:
                tps.append(v)
    return tps


def parse_signal(
    text: str,
    *,
    source: SignalSource = SignalSource.TELEGRAM,
    source_name: str = "",
    source_chat_id: Optional[int] = None,
) -> Optional[ParsedSignal]:
    """Parse free-text into a ParsedSignal, or None if not a trade call."""
    if not text or not text.strip():
        return None
    if not _SIGNAL_HINT_RE.search(text):
        return None
    # Skip cancels/closes/results and truncated quote previews — not fresh entries.
    if _NOISE_RE.search(text) or _QUOTE_PREVIEW_RE.search(text):
        return None

    # --- side ---
    side: Optional[SignalSide] = None
    m = _SIDE_RE.search(text)
    if m:
        side = _normalize_side(m.group(1))
    if side is None:
        m = _SIDE_INLINE_RE.search(text)
        if m:
            side = _normalize_side(m.group(1))
    if side is None:
        return None

    # --- pair ---
    base = quote = None
    m = _PAIR_RE.search(text)
    if m and m.group(1).upper() not in ("LONG", "SHORT", "BUY", "SELL", "TP", "SL"):
        base, quote = m.group(1), m.group(2)
    if not base:
        m = _PAIR_INLINE_RE.search(text)
        if m:
            base, quote = m.group(1), m.group(2)
    if not base:
        m = _PAIR_CONCAT_RE.search(text)
        if m and m.group(1).upper() not in ("LONG", "SHORT", "BUY", "SELL", "TP", "SL"):
            base, quote = m.group(1), m.group(2)
    if not base:
        m = _PAIR_CASHTAG_RE.search(text)
        if m and m.group(1).upper() not in ("LONG", "SHORT", "BUY", "SELL", "TP", "SL", "RR"):
            base, quote = m.group(1), None
    symbol = _normalize_symbol(base, quote) if base else ""
    if not symbol:
        return None

    # --- entry zone (or market entry when no explicit price is given) ---
    m = _ENTRY_NOW_ZONE_RE.search(text)
    entry_low = entry_high = None
    if m:
        entry_a = _to_float(m.group(1))
        entry_b = _to_float(m.group(2))
        if entry_a is not None and entry_b is not None:
            entry_low = min(entry_a, entry_b)
            entry_high = max(entry_a, entry_b)
    if entry_low is None:
        m = _ENTRY_ENUM_RE.search(text)
        if m:
            entry_a = _to_float(m.group(1))
            entry_b = _to_float(m.group(2))
            if entry_a is not None and entry_b is not None:
                entry_low = min(entry_a, entry_b)
                entry_high = max(entry_a, entry_b)
    if entry_low is None:
        m = _ENTRY_RE.search(text)
    if m and entry_low is None:
        # Regex has two alternatives: groups (1,2) = range, group (3) = single.
        if m.group(1) is not None:
            entry_a = _to_float(m.group(1))
            entry_b = _to_float(m.group(2)) if m.group(2) else None
        else:
            entry_a = _to_float(m.group(3))
            entry_b = None
        if entry_a is not None:
            entry_low = entry_a if entry_b is None else min(entry_a, entry_b)
            entry_high = entry_a if entry_b is None else max(entry_a, entry_b)
    if entry_low is None:
        # Numbered-list entry format: "Entry Targets:\n1) 2.24\n2) 2.30"
        if _ENTRY_LIST_LABEL_RE.search(text):
            m = _ENTRY_LIST_LABEL_RE.search(text)
            if m:
                remainder = text[m.end():]
                entries = []
                for nm in _ENTRY_LIST_NUMBER_RE.finditer(remainder):
                    v = _to_float(nm.group(1))
                    if v is not None:
                        entries.append(v)
                    if len(entries) >= 2:
                        break
                if entries:
                    entry_low = min(entries)
                    entry_high = max(entries)
    if entry_low is None:
        # "Entry now/market" with no price -> market entry; the live price is
        # filled in later by the normalizer (needs metrics). Require a stop or
        # target so it is still a real, actionable call.
        if _ENTRY_MARKET_RE.search(text):
            entry_low = entry_high = 0.0
        else:
            return None

    # --- stop loss ---
    sl = None
    m = _SL_RE.search(text)
    if m:
        candidate = _to_float(m.group(1))
        # Reject if it's just a TP/SL index (1,2,3...) - likely a numbered list label
        if candidate is not None and candidate > 10:
            sl = candidate
    # Numbered-list SL format: "Stop Target:\n1) 2.37"
    if sl is None and _SL_LIST_LABEL_RE.search(text):
        m = _SL_LIST_LABEL_RE.search(text)
        if m:
            remainder = text[m.end():]
            for nm in _SL_LIST_NUMBER_RE.finditer(remainder):
                v = _to_float(nm.group(1))
                if v is not None:
                    sl = v
                    break

    # --- take profits ---
    take_profits = _extract_take_profits(text)
    # Drop implausible values (e.g. leftover TP indices like 1,2,3 or junk):
    # a real target sits within an order of magnitude of the entry zone.
    if take_profits and entry_high > 0:
        lo_bound = entry_high * 0.1
        hi_bound = entry_high * 10.0
        take_profits = [t for t in take_profits if lo_bound <= t <= hi_bound]

    # --- leverage ---
    leverage = None
    m = _LEVERAGE_RE.search(text)
    if m:
        leverage = _to_float(m.group(1))
    if leverage is None:
        m = _LEVERAGE_INLINE_RE.search(text)
        if m:
            leverage = _to_float(m.group(1))

    # --- entry type (limit vs market) ---
    entry_type = "limit" if _LIMIT_RE.search(text) else "market"

    # --- timeframe ---
    timeframe = None
    m = _TIMEFRAME_LABEL_RE.search(text)
    if m:
        timeframe = _normalize_tf(m.group(1), m.group(2))
    if timeframe is None:
        m = _TIMEFRAME_INLINE_RE.search(text)
        if m:
            timeframe = _normalize_tf(m.group(1), m.group(2))

    return ParsedSignal(
        symbol=symbol,
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=sl,
        take_profits=take_profits,
        leverage=leverage,
        entry_type=entry_type,
        timeframe=timeframe,
        source=source,
        source_name=source_name,
        source_chat_id=source_chat_id,
        raw_text=text[:2000],
    )
