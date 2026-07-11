from __future__ import annotations

from typing import Any, Dict

from signal_copy.signal_copy_config import DRY_RUN


def format_price(price: float) -> str:
    if price is None or price == 0:
        return "0"
    price = float(price)
    if price < 0.0001:
        return f"{price:.8f}"
    if price < 0.001:
        return f"{price:.6f}"
    if price < 1:
        return f"{price:.4f}"
    if price < 100:
        return f"{price:.2f}"
    return f"{price:.0f}"


def get_tradingview_link(symbol: str) -> str:
    """Generate TradingView link for symbol (e.g., BTCUSDT -> BINANCE:BTCUSDT.P)"""
    clean_symbol = str(symbol).strip().upper()
    if clean_symbol.endswith(".P"):
        tv_symbol = clean_symbol
    elif clean_symbol.endswith("USDT"):
        tv_symbol = f"{clean_symbol}.P"
    else:
        tv_symbol = f"{clean_symbol}USDT.P"
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{tv_symbol}"


def _mode_footer() -> str:
    mode_str = "Testnet" if DRY_RUN else "LIVE"
    return f"Fusion Signal Copy • {mode_str}"


def _safe_float(payload: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except Exception:
                continue
    return default


def _safe_int(payload: Dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return int(payload[key])
            except Exception:
                continue
    return default


def _safe_str(payload: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return default


def _safe_bool(payload: Dict[str, Any], *keys: str, default: bool = False) -> bool:
    for key in keys:
        if key in payload and payload[key] is not None:
            value = payload[key]
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
    return default


def _priority_badge(payload: Dict[str, Any]) -> str:
    labels = []
    priority_score = _safe_float(payload, "priority_score", default=0.0)
    is_medium_priority = _safe_bool(payload, "is_medium_priority", default=False)

    if is_medium_priority:
        labels.append("⭐ MEDIUM PRIORITY")
    if priority_score >= 90:
        labels.append("🔥 HIGH PRIORITY")

    return "\n".join(labels)


def _build_vip_block(payload: Dict[str, Any]) -> str:
    vip_status = _safe_str(payload, "vip_status", default="NONE").upper()
    vip_bias = _safe_str(payload, "vip_directional_bias", default="NEUTRAL").upper()

    vip_line = f"⭐ <b>VIP</b>: {vip_status} • <b>{vip_bias}</b>"
    parts = [vip_line]

    precursor = _safe_str(payload, "precursor_state", default="").upper()
    precursor_score = _safe_float(payload, "precursor_score", default=0.0)
    if precursor:
        parts.append(f"🕵️ <b>Precursor</b>: <b>{precursor}</b> • Score <b>{precursor_score:.1f}</b>")

    vip_note = _safe_str(payload, "vip_note", "vip_reason", default="")
    if vip_note:
        parts.append(f"📝 <b>{vip_note}</b>")

    return "\n".join(parts)


def _build_silent_accumulation_block(payload: Dict[str, Any]) -> str:
    silent_status = _safe_str(
        payload,
        "silent_accumulation_status",
        "sa_status",
        default="",
    ).upper()
    if not silent_status:
        return ""

    score = _safe_float(
        payload,
        "silent_accumulation_score",
        "sa_score",
        default=0.0,
    )
    price_range_pct = _safe_float(
        payload,
        "silent_accumulation_range_pct",
        "sa_range_pct",
        default=0.0,
    )
    vol_vs_baseline = _safe_float(
        payload,
        "silent_accumulation_vol_vs_baseline",
        "sa_vol_vs_baseline",
        default=0.0,
    )
    bullish_close = _safe_float(
        payload,
        "silent_accumulation_bullish_close_pct",
        "sa_bullish_close_pct",
        default=0.0,
    )
    lower_rejection = _safe_float(
        payload,
        "silent_accumulation_lower_rejection_pct",
        "sa_lower_rejection_pct",
        default=0.0,
    )
    reason = _safe_str(
        payload,
        "silent_accumulation_reason",
        "sa_reason",
        default="NONE",
    )

    return "\n".join(
        [
            f"🕵️ Silent Accumulation: <b>{silent_status}</b> • Score {score:.1f}",
            f"📦 Range Compression: {price_range_pct:.2f}%",
            f"🔊 Vol vs Baseline: {vol_vs_baseline:.2f}x",
            f"🧲 Bullish Close: {bullish_close * 100:.0f}% | Lower Rejection: {lower_rejection * 100:.0f}%",
            f"📝 <code>{reason}</code>",
        ]
    )


def _btc_weight_value(payload: Dict[str, Any]) -> float:
    return _safe_float(payload, "btc_weight", "btc_influence_weight", default=0.50)


def _btc_weight_label(payload: Dict[str, Any], weight: float | None = None) -> str:
    explicit = _safe_str(
        payload,
        "btc_weight_label",
        "btc_corr_regime_note",
        default="",
    ).upper()
    if explicit:
        mapping = {
            "FOLLOWING_BTC": "FOLLOWING_BTC",
            "FOLLOWING": "FOLLOWING_BTC",
            "PARTIAL_DECOUPLE": "PARTIAL_DECOUPLE",
            "PARTIAL_DECOUPLED": "PARTIAL_DECOUPLE",
            "DECOUPLED": "DECOUPLED",
            "NEGATIVE_RELATION": "DECOUPLED",
            "SELF_BTC": "FOLLOWING_BTC",
            "BTC_CORR_UNAVAILABLE": "NEUTRAL",
            "UNKNOWN": "NEUTRAL",
        }
        return mapping.get(explicit, explicit)

    w = _btc_weight_value(payload) if weight is None else float(weight)
    if w >= 0.70:
        return "FOLLOWING_BTC"
    if w >= 0.40:
        return "PARTIAL_DECOUPLE"
    if w <= 0.15:
        return "DECOUPLED"
    return "NEUTRAL"


def _normalize_btc_context(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if "BEAR" in raw:
        return "BEAR_CONFIRMED"
    if "BULL" in raw:
        return "BULL_CONFIRMED"
    return "NEUTRAL"


def _btc_context_label(payload: Dict[str, Any]) -> str:
    raw = _safe_str(
        payload,
        "btc_context",
        "btc_fast_state",
        "btc_context_state",
        "btc_bias_macro",
        "btc_bias",
        default="NEUTRAL",
    )
    return _normalize_btc_context(raw)


def build_parser_report(
    sig: Any,
    result: Any,
    cls: Any = None,
    source_name: str = "",
) -> str:
    """Build rich parser validation report with market data for the parser channel."""
    symbol = getattr(sig, "symbol", "UNKNOWN").upper()
    side = getattr(sig, "side", "UNKNOWN")
    side_str = side.value if hasattr(side, "value") else str(side).upper()

    entry_low = getattr(sig, "entry_low", 0.0)
    entry_high = getattr(sig, "entry_high", 0.0)
    entry_mid = getattr(sig, "entry_mid", 0.0)
    stop_loss = getattr(sig, "stop_loss", 0.0)
    take_profits = getattr(sig, "take_profits", [])
    leverage = getattr(sig, "leverage", 0.0)
    risk_pct = getattr(sig, "risk_pct", 0.0)

    verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)
    score = getattr(result, "score", 0.0)

    # Get metrics from result
    metrics = getattr(result, "metrics", {}) or {}
    price = metrics.get("price", 0.0)
    rsi = metrics.get("rsi", 0.0)
    cvd = metrics.get("cvd", metrics.get("cvd_zscore", 0.0))
    oi_15m = metrics.get("oi_change_15m_pct", 0.0)
    oi_1h = metrics.get("oi_change_1h_pct", 0.0)
    funding = metrics.get("funding_rate", 0.0)
    poc = metrics.get("poc", 0.0)
    vol_ratio = metrics.get("vol_ratio", 0.0)
    regime = metrics.get("regime_label", "UNKNOWN")
    quadrant = "UNKNOWN"  # would need from metrics
    btc_corr = metrics.get("btc_correlation", 0.0)
    btc_bias = metrics.get("btc_bias", "NEUTRAL")

    icon = "🟢" if side_str == "LONG" else "🔴"
    verdict_icon = {"VALID": "✅", "WEAK": "⚠️", "REJECT": "❌"}.get(verdict, "❓")

    # Format entry range
    if entry_low and entry_high:
        entry_str = f"{format_price(entry_low)} - {format_price(entry_high)}"
    else:
        entry_str = format_price(entry_mid)

    # Format TPs
    tp_str = ", ".join(format_price(tp) for tp in take_profits[:3]) if take_profits else "N/A"
    tp1 = take_profits[0] if len(take_profits) > 0 else 0.0
    tp_full = take_profits[-1] if len(take_profits) > 1 else (take_profits[0] if take_profits else 0.0)

    # Risk/Reward
    if entry_mid > 0 and stop_loss > 0:
        risk = abs(entry_mid - stop_loss)
        rr1 = abs(tp1 - entry_mid) / risk if risk > 0 and tp1 > 0 else 0
        rr_full = abs(tp_full - entry_mid) / risk if risk > 0 and tp_full > 0 else 0
        rr_str = f"RR: TP1 {rr1:.2f} | Full {rr_full:.2f}"
    else:
        rr_str = "RR: N/A"

    tv_link = get_tradingview_link(symbol)

    lines = [
        f"{verdict_icon} <b>VALIDASI {verdict}</b> {icon} {side_str} {symbol}",
        f"📡 Sumber: {source_name or 'Unknown'}",
        f"💰 Entry: {entry_str}",
        f"🎯 TP: {tp_str}",
        f"🛑 SL: {format_price(stop_loss)}",
        f"⚖️ {rr_str}",
        f"⚙️ Leverage: {leverage:.0f}x | Risk: {risk_pct*100:.1f}%",
        f"📊 Score: {score:+.1f}/100",
    ]

    # Market data block
    lines.extend([
        "────────────────────",
        f"📈 CVD: {cvd:,.0f} {'🟢' if cvd >= 0 else '🔴'}",
        f"📉 OI 15m: {oi_15m:+.2f}% | OI 1h: {oi_1h:+.2f}%",
        f"💸 Funding: {funding:+.4f}%",
        f"🎛️ POC: {format_price(poc)} | VolRatio: {vol_ratio:.2f} | RSI: {rsi:.1f}",
        f"⚙️ Regime: {regime} | Kuadran: {quadrant}",
        f"🧭 BTC Bias: {btc_bias} | Corr: {btc_corr:.2f}",
        f'🔗 <a href="{tv_link}">📊 Lihat Chart TradingView</a>',
        "────────────────────",
        _mode_footer(),
    ])

    return "\n".join(lines)


def build_execution_message(
    outcome: Any,
    sig: Any,
    result: Any,
) -> str:
    """Build execution result message (entry/TP/SL) for the trades channel."""
    symbol = getattr(sig, "symbol", "UNKNOWN").upper()
    side = getattr(sig, "side", "UNKNOWN")
    side_str = side.value if hasattr(side, "value") else str(side).upper()

    icon = "🟢" if side_str == "LONG" else "🔴"
    ok = getattr(outcome, "ok", False)
    reason = getattr(outcome, "reason", "UNKNOWN")

    entry_price = getattr(outcome, "entry_price", 0.0)
    notional = getattr(outcome, "notional", 0.0)
    sl_price = getattr(outcome, "sl_price", 0.0)
    tp1 = getattr(outcome, "tp1", 0.0)
    tp_full = getattr(outcome, "tp_full", 0.0)
    risk_amount = getattr(outcome, "risk_amount", 0.0)

    if ok:
        header = f"✅ <b>EXECUTED</b> {icon} {side_str} {symbol}"
    else:
        header = f"❌ <b>EXECUTION FAILED</b> {icon} {side_str} {symbol}"

    tv_link = get_tradingview_link(symbol)

    lines = [
        header,
        f"💰 Entry: {format_price(entry_price)}",
        f"🎯 TP1: {format_price(tp1)} | Full: {format_price(tp_full)}",
        f"🛑 SL: {format_price(sl_price)}",
        f"📊 Notional: ${notional:.0f} | Risk: ${risk_amount:.2f}",
        f"📌 Status: {reason}",
    ]

    if not ok:
        lines.append(f"❗ Reason: {reason}")

    lines.extend([
        "────────────────────",
        f'🔗 <a href="{tv_link}">📊 Lihat Chart TradingView</a>',
        "────────────────────",
        _mode_footer(),
    ])

    return "\n".join(lines)


def build_close_message(payload: Dict[str, Any]) -> str:
    """Build position close message (TP/SL/manual) for the trades channel."""
    symbol = _safe_str(payload, "symbol", default="UNKNOWN").upper()
    side = _safe_str(payload, "side", "direction", default="LONG").upper()
    reason = _safe_str(payload, "reason", default="UNKNOWN")
    normalized_reason = _safe_str(payload, "normalized_reason", default=reason)
    raw_reason = _safe_str(payload, "raw_engine_reason", "raw_reason", default="")

    exit_price = _safe_float(payload, "exit_price", "price", default=0.0)
    pnl_pct = _safe_float(payload, "pnl_pct", default=0.0)
    pnl_amount = _safe_float(payload, "pnl_amount", "pnl_usd", default=0.0)
    hold_minutes = _safe_float(payload, "hold_minutes", default=0.0)
    equity = _safe_float(payload, "equity", "balance_after", default=0.0)

    partial = _safe_bool(payload, "is_partial", default=False)
    partial_fraction = _safe_float(payload, "partial_fraction", default=0.0)

    original_sl = _safe_float(payload, "original_sl", "sl_original", default=0.0)
    active_stop = _safe_float(payload, "active_stop", "active_sl_at_exit", default=0.0)
    stop_kind = _safe_str(payload, "stop_kind", "sl_kind_at_exit", default="").upper()

    icon = "🟢" if side == "LONG" else "🔴"

    if pnl_pct > 0:
        header_icon = "✅"
    elif pnl_pct < 0:
        header_icon = "❌"
    else:
        header_icon = "✅"

    if partial and partial_fraction > 0:
        pct = partial_fraction * 100.0
        title = f"{header_icon} <b>CLOSE (Partial {pct:.0f}%)</b> {icon} {side} {symbol}"
    else:
        title = f"{header_icon} <b>CLOSE</b> {icon} {side} {symbol}"

    lines = [
        title,
        f"💰 Exit: {format_price(exit_price)}",
        f"📊 PnL: <b>{pnl_pct:+.2f}%</b> (${pnl_amount:+.2f})",
        f"⏱️ Hold: {hold_minutes:.1f} min",
        f"💼 Equity: ${equity:.2f}",
        f"📌 Reason: {normalized_reason}",
    ]

    if original_sl > 0:
        lines.append(f"🛑 Original SL: {format_price(original_sl)}")
    if active_stop > 0:
        stop_label = stop_kind if stop_kind else "ACTIVE"
        lines.append(f"🎯 Active Stop at Exit: {format_price(active_stop)} ({stop_label})")
    if raw_reason and raw_reason != normalized_reason:
        lines.append(f"🧾 Raw Engine Reason: {raw_reason}")

    tv_link = get_tradingview_link(symbol)
    lines.extend([
        "────────────────────",
        f'🔗 <a href="{tv_link}">📊 Lihat Chart TradingView</a>',
        "────────────────────",
        _mode_footer(),
    ])

    return "\n".join(lines)


def build_whale_alert_message(payload: Dict[str, Any]) -> str:
    title = _safe_str(payload, "title", default="WHALE ALERT")
    symbol = _safe_str(payload, "symbol", default="UNKNOWN").upper()
    side = _safe_str(payload, "side", "direction", default="UNKNOWN").upper()
    size_usd = _safe_float(payload, "size_usd", "notional", default=0.0)
    price = _safe_float(payload, "price", "entry_price", default=0.0)
    message = _safe_str(payload, "message", default="")
    tv_link = get_tradingview_link(symbol)

    lines = [
        f"🐋 <b>{title}</b>",
        f"📊 Symbol: {symbol}",
    ]

    if side and side != "UNKNOWN":
        lines.append(f"🧭 Side: {side}")
    if size_usd > 0:
        lines.append(f"💰 Size: ${size_usd:,.0f}")
    if price > 0:
        lines.append(f"🏷️ Price: {format_price(price)}")
    if message:
        lines.append(message)

    lines.extend([
        f'🔗 <a href="{tv_link}">📊 Lihat Chart TradingView</a>',
        "────────────────────",
        _mode_footer(),
    ])
    return "\n".join(lines)


__all__ = [
    "format_price",
    "get_tradingview_link",
    "build_parser_report",
    "build_execution_message",
    "build_close_message",
    "build_whale_alert_message",
]