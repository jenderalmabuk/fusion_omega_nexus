"""ExecutionGateway — ONE RiskManager + ONE trader for every signal source.

This is the "one hand" of the system. It wraps the components that already
exist and are battle-tested in the repo:

    risk/risk_engine.py            -> RiskManager   (unified portfolio limits)
    execution/binance_testnet_trader.py -> trader   (submit_open contract,
                                                     partial TP, trailing, …)

Flow for every OrderIntent, regardless of which engine sent it:

    validate -> risk gate (can_open_new_position) -> size (if needed)
             -> reserve_open_risk -> trader.submit_open -> commit / release

This mirrors exactly what signal_copy/executor.py already does — the gateway
simply makes that path the ONLY path, shared by all engines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gateway.order_intent import OrderIntent

logger = logging.getLogger("gateway")


@dataclass
class GatewayResult:
    ok: bool
    reason: str
    intent_id: str = ""
    symbol: str = ""
    side: str = ""
    notional: float = 0.0
    risk_amount: float = 0.0
    trader_response: Optional[Dict[str, Any]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "intent_id": self.intent_id,
            "symbol": self.symbol,
            "side": self.side,
            "notional": self.notional,
            "risk_amount": self.risk_amount,
            "trader_response": self.trader_response,
        }


class ExecutionGateway:
    """Single choke-point for order execution.

    Args:
        trader:   any object exposing the fusion async ``submit_open(**kwargs)``
                  contract (BinanceTestnetTrader today; swappable later).
        risk_mgr: risk.risk_engine.RiskManager instance (the ONE portfolio).
        submit_timeout_sec: how long to wait for the trader router queue.
    """

    def __init__(self, trader: Any, risk_mgr: Any, *, submit_timeout_sec: float = 30.0):
        self.trader = trader
        self.risk_mgr = risk_mgr
        self.submit_timeout_sec = float(submit_timeout_sec)
        self.history: List[Dict[str, Any]] = []   # last N results, for /portfolio & debugging
        self._history_max = 200

    # ── public API ───────────────────────────────────────────

    async def execute(self, intent: OrderIntent, *, dry_run: bool = False) -> GatewayResult:
        """Run one intent through the full unified pipeline."""
        res = GatewayResult(ok=False, reason="", intent_id=intent.intent_id,
                            symbol=intent.symbol, side=str(intent.side).upper())

        # 1) validate the contract itself
        err = intent.validate()
        if err:
            res.reason = f"invalid intent: {err}"
            return self._record(intent, res)

        symbol = intent.symbol
        side = str(intent.side).upper()
        entry = float(intent.entry_price)
        sl = float(intent.sl_price)

        # 2) unified portfolio gate — the whole point of the gateway.
        #    Every engine's trade passes the SAME limits: daily loss, exposure,
        #    cluster, cooldown, max positions.
        try:
            gate = self.risk_mgr.check_risk_limits(symbol=symbol, is_vip=intent.is_vip, side=side)
        except Exception as exc:  # never let a risk-engine bug open an unchecked trade
            res.reason = f"risk check error: {exc}"
            return self._record(intent, res)
        if not gate.get("can_trade", False):
            res.reason = f"blocked by portfolio risk: {gate.get('reason', 'unknown')}"
            return self._record(intent, res)

        # 3) sizing — respect the intent's own SL for risk math
        notional, risk_amount, size_err = self._size(intent, entry, sl, side)
        if size_err:
            res.reason = size_err
            return self._record(intent, res)
        res.notional = notional
        res.risk_amount = risk_amount

        # 4) build the trader payload (same keys signal_copy already uses,
        #    so BinanceTestnetTrader needs ZERO changes)
        payload = self._build_payload(intent, entry, sl, side, notional, risk_amount)

        if dry_run:
            res.ok = True
            res.reason = "DRY_RUN (no order placed)"
            logger.info("[GATEWAY] DRY_RUN %s src=%s %s entry=%.6f notional=%.2f sl=%.6f",
                        symbol, intent.source, side, entry, notional, sl)
            return self._record(intent, res)

        # 5) reserve -> submit -> commit/release (identical semantics to signal_copy)
        reserved = False
        try:
            reserved = await self.risk_mgr.reserve_open_risk(symbol, risk_amount)
        except Exception as exc:
            logger.warning("[GATEWAY] risk reserve error %s: %s", symbol, exc)
        if not reserved:
            res.reason = "risk reservation blocked (parallel open-risk budget)"
            return self._record(intent, res)

        try:
            opened = await self.trader.submit_open(timeout_sec=self.submit_timeout_sec, **payload)
        except Exception as exc:
            logger.exception("[GATEWAY] submit_open failed %s: %s", symbol, exc)
            await self._safe_release(symbol)
            res.reason = f"submit_open error: {exc}"
            return self._record(intent, res)

        if not opened:
            await self._safe_release(symbol)
            res.reason = "trader rejected/blocked the open (see trader logs)"
            return self._record(intent, res)

        try:
            await self.risk_mgr.commit_open_trade(symbol, risk_amount=risk_amount, is_vip=intent.is_vip)
        except Exception as exc:
            logger.warning("[GATEWAY] commit_open_trade error %s: %s", symbol, exc)

        res.ok = True
        res.reason = "opened"
        res.trader_response = opened if isinstance(opened, dict) else {"raw": str(opened)}
        logger.info("[GATEWAY] OPENED %s src=%s %s notional=%.2f risk=%.2f intent=%s",
                    symbol, intent.source, side, notional, risk_amount, intent.intent_id)
        return self._record(intent, res)

    def portfolio(self) -> Dict[str, Any]:
        """One unified view of the whole book — every engine included."""
        rm = self.risk_mgr
        out: Dict[str, Any] = {}
        for name, fn in (
            ("equity", "get_current_equity"),
            ("daily_pnl_pct", "get_daily_pnl_pct"),
            ("total_exposure_pct", "get_total_exposure_pct"),
            ("reserved_risk_total", "get_reserved_risk_total"),
        ):
            try:
                out[name] = float(getattr(rm, fn)())
            except Exception:
                out[name] = None
        try:
            out["open_position_count"] = int(rm._position_count())
            out["open_symbols"] = sorted(rm._position_symbols())
        except Exception:
            out["open_position_count"] = None
            out["open_symbols"] = []
        try:
            out["daily_loss_limit_hit"] = bool(rm.is_daily_loss_limit_hit())
            out["exposure_limit_exceeded"] = bool(rm.is_exposure_limit_exceeded())
        except Exception:
            pass
        out["recent_intents"] = self.history[-20:]
        return out

    # ── internals ────────────────────────────────────────────

    def _size(self, intent: OrderIntent, entry: float, sl: float, side: str):
        """Return (notional, risk_amount, error)."""
        sl_frac = abs(entry - sl) / entry
        if sl_frac <= 0:
            return 0.0, 0.0, "SL distance is zero"

        # Per-position notional cap: no single trade may consume more than
        # max_notional_pct of equity. This is the fix for the exposure-lock bug
        # where an explicit `notional` (from signal_copy sizing) bypassed the cap
        # entirely and one BTC position ate 91% of the book. The cap now applies
        # to BOTH the explicit-notional path and the risk_pct path.
        # Config: MAX_NOTIONAL_PCT_OF_BALANCE. Value may be stored as a percent
        # (e.g. 20.0) or a fraction (0.20) -> normalize both to a fraction.
        try:
            equity = float(self.risk_mgr.get_current_equity())
        except Exception as exc:
            return 0.0, 0.0, f"cannot read equity: {exc}"
        _mnp = getattr(self.risk_mgr, "max_notional_pct", 0.20)
        _frac = (_mnp / 100.0) if _mnp > 1 else _mnp
        _frac = _frac if _frac > 0 else 0.20
        cap = max(10.0, equity * _frac) if equity > 0 else None

        if intent.notional is not None:
            notional = float(intent.notional)
            if cap is not None:
                notional = min(notional, cap)
            return notional, notional * sl_frac, None

        # size from risk_pct against the INTENT's own stop (like signal_copy does)
        if equity <= 0:
            return 0.0, 0.0, "equity is zero"

        risk_budget = equity * float(intent.risk_pct)
        notional = risk_budget / sl_frac
        if cap is not None:
            notional = min(notional, cap)
        notional = max(10.0, notional)
        return notional, notional * sl_frac, None

    def _build_payload(self, intent: OrderIntent, entry: float, sl: float,
                       side: str, notional: float, risk_amount: float) -> Dict[str, Any]:
        tps = [float(t) for t in (intent.tps or [])]
        tp_payload = {f"tp{i}": tp for i, tp in enumerate(tps, start=1)}
        tp_full = tps[-1] if tps else 0.0
        payload: Dict[str, Any] = {
            "symbol": intent.symbol,
            "side": side,
            "direction": side,
            "entry_price": entry,
            "sl_price": sl,
            "sl": sl,
            **tp_payload,
            "tp_full": tp_full,
            "notional": notional,
            "size_usd": notional,
            "base_notional": notional,
            "regime": str(intent.regime or "TRENDING"),
            "confidence": float(intent.confidence),
            "actual_risk_amount": risk_amount,
            "risk_amount": risk_amount,
            "planned_risk_amount": risk_amount,
            "source": intent.source,
            "signal_id": intent.intent_id,
            "tag": intent.tag,
            "adv": dict(intent.adv_snapshot or {}),
            "adv_snapshot": dict(intent.adv_snapshot or {}),
        }
        if intent.leverage:
            payload["leverage"] = int(intent.leverage)
        return payload

    async def _safe_release(self, symbol: str) -> None:
        try:
            await self.risk_mgr.release_open_risk(symbol)
        except Exception:
            pass

    def _record(self, intent: OrderIntent, res: GatewayResult) -> GatewayResult:
        self.history.append({
            "intent": intent.to_dict(),
            "result": {k: v for k, v in res.to_dict().items() if k != "trader_response"},
        })
        if len(self.history) > self._history_max:
            self.history = self.history[-self._history_max:]
        if not res.ok:
            logger.info("[GATEWAY] REJECTED %s src=%s: %s", intent.symbol, intent.source, res.reason)
        return res
