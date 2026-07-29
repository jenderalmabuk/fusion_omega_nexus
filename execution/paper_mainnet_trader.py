"""Paper Execution Engine (Mainnet-Priced)

Drop-in replacement for BinanceTestnetTrader in the gateway. Instead of routing
orders to Binance TESTNET (whose orderbook/mark price diverge wildly from the
real market and cause fake instant stop-outs), this fills and manages positions
against REAL mainnet prices (via Nexus FastAPI /klines/binance). No real orders
are placed — PnL is a faithful paper simulation of what mainnet would have done.

Contract compatibility with gateway/service.py + run_gateway.py:
- async submit_open(timeout_sec=..., **payload) -> dict|None   (truthy == opened)
- async start()  / async stop()                                (lifecycle loops)

payload keys (same as testnet trader): symbol, side, entry_price, sl / sl_price,
tp1, tp3 / tp_full, notional / size_usd, leverage, regime, adv_snapshot.

ponytail: slippage/fees = 0 for paper clarity; add taker 0.05% + slip 0.05%
before trusting numbers for live sizing. → add when moving to real money.
"""
from __future__ import annotations
import asyncio, json, logging, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
import aiohttp

PAPER_STATE_PATH = Path(os.getenv("FQ_PAPER_POSITION_STATE", "runtime/fusion_quantum/journal/open_positions.json"))
PAPER_EQUITY_PATH = Path(os.getenv("FQ_PAPER_EQUITY_STATE", "runtime/fusion_quantum/journal/paper_equity.json"))
SHADOW_TRAILING_PATH = Path(os.getenv("SHADOW_TRAILING_JOURNAL", "journal/shadow_trailing.jsonl"))


def persist_equity(equity: float) -> None:
    """Keep realized paper equity across gateway restarts."""
    PAPER_EQUITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PAPER_EQUITY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"equity": float(equity)}))
    os.replace(tmp, PAPER_EQUITY_PATH)


def load_equity():
    try:
        return float(json.loads(PAPER_EQUITY_PATH.read_text())["equity"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None

def _jsonable(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _persist_positions(positions):
    PAPER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PAPER_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(positions, default=_jsonable, separators=(",", ":")))
    os.replace(tmp, PAPER_STATE_PATH)


def _load_positions():
    try:
        data = json.loads(PAPER_STATE_PATH.read_text())
        for pos in data.values():
            pos["opened_at"] = datetime.fromisoformat(pos["opened_at"])
        return data
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {}


def _append_shadow_result(row: Dict[str, Any]) -> None:
    """Append one immutable baseline-vs-shadow result at actual position close."""
    SHADOW_TRAILING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_TRAILING_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=_jsonable, separators=(",", ":")) + "\n")

UTC = timezone.utc
logger = logging.getLogger("gateway.paper")


class PaperMainnetTrader:
    """Paper trader using mainnet prices for accurate edge validation."""

    def __init__(self, nexus_api: str = "http://fastapi:8000", poll_interval: float = 3.0):
        self.nexus_api = nexus_api.rstrip("/")
        self.poll_interval = float(poll_interval)
        self.positions: Dict[str, Dict[str, Any]] = _load_positions()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._journal = None
        self.risk_mgr: Any = None  # set by run_gateway wiring; enables realized-PnL equity sync

    # ── lifecycle ────────────────────────────────────────────────
    async def start(self):
        if self._running:
            return
        from execution.trade_journal import TradeJournalWriter
        self._running = True
        self._session = aiohttp.ClientSession()
        self._journal = TradeJournalWriter()
        await self._journal.start()
        self._task = asyncio.create_task(self._management_loop(), name="paper_mgmt")
        logger.info("[PAPER] started — mainnet-priced fills (poll=%.1fs)", self.poll_interval)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._journal:
            await self._journal.shutdown()
        if self._session:
            await self._session.close()
        logger.info("[PAPER] stopped")

    # ── helpers ──────────────────────────────────────────────────
    async def _get_mark_price(self, symbol: str) -> float:
        """Last 1m close from mainnet Binance (via Nexus FastAPI)."""
        if self._session is None:
            return 0.0
        try:
            url = f"{self.nexus_api}/klines/binance/{symbol}?tf=1m&limit=1"
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                if r.status == 200:
                    data = await r.json()
                    # Nexus returns {"exchange":"binance","symbol":"...","data":[...]}
                    if isinstance(data, dict) and "data" in data:
                        bars = data["data"]
                        if isinstance(bars, list) and bars:
                            return float(bars[-1]["close"])
                    # fallback: old format (direct list)
                    elif isinstance(data, list) and data:
                        return float(data[-1]["close"])
        except Exception as e:
            logger.warning("[PAPER] mark_price fetch fail %s: %s", symbol, e)
        return 0.0

    @staticmethod
    def _adverse_fill(price: float, side: str, *, opening: bool) -> float:
        slip = max(0.0, float(os.getenv("PAPER_SLIPPAGE_PCT", "0.0005")))
        buy = (side == "LONG" and opening) or (side == "SHORT" and not opening)
        return float(price) * (1.0 + slip if buy else 1.0 - slip)

    @staticmethod
    def _fee(notional: float) -> float:
        rate = max(0.0, float(os.getenv("PAPER_TAKER_FEE_PCT", "0.0005")))
        return abs(float(notional)) * rate

    def _apply_equity_delta(self, delta: float) -> Optional[float]:
        if self.risk_mgr is None or not hasattr(self.risk_mgr, "sync_balance"):
            return None
        equity_after = float(self.risk_mgr.get_current_equity()) + float(delta)
        self.risk_mgr.sync_balance(equity_after)
        persist_equity(equity_after)
        return equity_after

    @staticmethod
    def _shadow_enabled() -> bool:
        return os.getenv("SHADOW_TRAILING_ENABLED", "true").lower() in ("1", "true", "yes")

    def _shadow_trail_pct(self, pos: Dict[str, Any]) -> float:
        fixed = max(0.0, float(os.getenv("SHADOW_TRAIL_MIN_PCT", "0.01")))
        atr = float((pos.get("adv_snapshot") or {}).get("atr_pct", 0) or 0) / 100.0
        atr_mult = max(0.0, float(os.getenv("SHADOW_TRAIL_ATR_MULTIPLIER", "1.0")))
        return max(fixed, atr * atr_mult)

    def _record_stale_conflict_shadow(self, pos: Dict[str, Any], mark: float) -> bool:
        """Record stale/conflict candidate; never close or mutate provider lifecycle."""
        if os.getenv("STALE_SHADOW_ENABLED", "true").lower() not in ("1", "true", "yes"):
            return False
        if pos.get("tp1_hit") is True or pos.get("shadow_stale_candidate"):
            return False
        opened = pos.get("opened_at")
        if not isinstance(opened, datetime):
            return False
        age_h = (datetime.now(UTC) - opened).total_seconds() / 3600.0
        conflicts = int((pos.get("adv_snapshot") or {}).get("stale_conflict_categories", 0) or 0)
        if age_h < 18.0 or conflicts < 3:
            return False
        pos["shadow_stale_candidate"] = True
        pos["shadow_stale_mark"] = float(mark)
        pos["shadow_stale_at"] = datetime.now(UTC)
        logger.info("[SHADOW_STALE] WOULD_EXIT %s %s @ %.6g age=%.1fh conflicts=%d",
                    pos.get("symbol"), pos.get("side"), mark, age_h, conflicts)
        return True

    def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
        """Counterfactual only. Never mutates provider SL, TP, qty, or execution."""
        if not self._shadow_enabled() or pos.get("shadow_exit_price"):
            return
        # Require explicit TP1 event. Never infer eligibility from restored
        # legacy next_tp_index because that contaminated prior shadow results.
        if pos.get("tp1_hit") is not True:
            return
        side = pos["side"]
        trail = self._shadow_trail_pct(pos)
        if not pos.get("shadow_trailing_active"):
            pos["shadow_trailing_active"] = True
            pos["shadow_activated_at"] = datetime.now(UTC)
            pos["shadow_high_watermark"] = mark
            pos["shadow_low_watermark"] = mark
        pos["shadow_high_watermark"] = max(float(pos.get("shadow_high_watermark", mark)), mark)
        pos["shadow_low_watermark"] = min(float(pos.get("shadow_low_watermark", mark)), mark)
        if side == "LONG":
            candidate = pos["shadow_high_watermark"] * (1.0 - trail)
            pos["shadow_stop"] = max(float(pos.get("shadow_stop", 0) or 0), candidate)
            hit = mark <= pos["shadow_stop"]
        else:
            candidate = pos["shadow_low_watermark"] * (1.0 + trail)
            old = float(pos.get("shadow_stop", 0) or 0)
            pos["shadow_stop"] = min(old, candidate) if old > 0 else candidate
            hit = mark >= pos["shadow_stop"]
        if not hit:
            return
        actual_exit = self._adverse_fill(pos["shadow_stop"], side, opening=False)
        qty = float(pos.get("qty", 0))
        gross = ((actual_exit - pos["entry_price"]) * qty if side == "LONG"
                 else (pos["entry_price"] - actual_exit) * qty)
        fee = self._fee(actual_exit * qty)
        pos["shadow_exit_price"] = actual_exit
        pos["shadow_exit_at"] = datetime.now(UTC)
        pos["shadow_remaining_net_pnl"] = gross - fee
        logger.info("[SHADOW_TRAIL] WOULD_EXIT %s %s @ %.6g stop=%.6g qty=%.8g",
                    pos["symbol"], side, actual_exit, pos["shadow_stop"], qty)

    # ── open ─────────────────────────────────────────────────────
    async def submit_open(self, timeout_sec: float = 30.0, **params) -> Optional[Dict[str, Any]]:
        del timeout_sec
        symbol = str(params.get("symbol", ""))
        side = str(params.get("side", "")).upper()
        sl_price = float(params.get("sl", params.get("sl_price", 0)) or 0)
        tp_ladder = []
        for i in range(1, 21):
            value = float(params.get(f"tp{i}", 0) or 0)
            if value:
                tp_ladder.append(value)
        if not tp_ladder and params.get("tp_full"):
            tp_ladder.append(float(params["tp_full"]))
        tp1 = tp_ladder[0] if tp_ladder else 0.0
        notional = float(params.get("notional", params.get("size_usd", 0)) or 0)
        leverage = int(params["leverage"]) if params.get("leverage") else 1
        regime = str(params.get("regime", "TRENDING"))

        if not symbol or side not in ("LONG", "SHORT"):
            logger.warning("[PAPER] invalid params symbol=%s side=%s", symbol, side)
            return None
        if symbol in self.positions:
            logger.info("[PAPER] %s already open — skip dup", symbol)
            return None

        mark = await self._get_mark_price(symbol)
        if mark <= 0:
            logger.warning("[PAPER] no mainnet price for %s — reject", symbol)
            return None
        requested_entry = float(params.get("entry_price", 0) or 0)
        raw_fill = requested_entry if str(params.get("tag", "")).startswith("fusion_quantum") and requested_entry > 0 else mark
        fill = self._adverse_fill(raw_fill, side, opening=True)

        # Limit already invalidated before gateway open: no synthetic perfect fill.
        if side == "LONG" and sl_price and mark <= sl_price:
            logger.warning("[PAPER] %s LONG mark %.6g <= SL %.6g — reject gap-through fill", symbol, mark, sl_price)
            return None
        if side == "SHORT" and sl_price and mark >= sl_price:
            logger.warning("[PAPER] %s SHORT mark %.6g >= SL %.6g — reject gap-through fill", symbol, mark, sl_price)
            return None
        if side == "LONG" and tp1 and mark >= tp1:
            logger.warning("[PAPER] %s LONG mark %.6g >= TP1 %.6g — reject stale fill", symbol, mark, tp1)
            return None
        if side == "SHORT" and tp1 and mark <= tp1:
            logger.warning("[PAPER] %s SHORT mark %.6g <= TP1 %.6g — reject stale fill", symbol, mark, tp1)
            return None

        # Sanity guard: reject if signal SL is on the wrong side of the fill
        # (protects against fake fills that would instant-stop).
        if side == "LONG" and sl_price >= fill:
            logger.warning("[PAPER] %s LONG SL %.6g >= fill %.6g — reject (instant-stop guard)", symbol, sl_price, fill)
            return None
        if side == "SHORT" and sl_price and sl_price <= fill:
            logger.warning("[PAPER] %s SHORT SL %.6g <= fill %.6g — reject (instant-stop guard)", symbol, sl_price, fill)
            return None


        qty = notional / fill if fill > 0 else 0
        entry_fee = self._fee(fill * qty)
        # Observability: capture the signal-copy enrichment snapshot (metrics,
        # score, confidence) at open so the trade journal isn't blank at close.
        # This is the metrics dict the executor forwards as `adv_snapshot`
        # (price/cvd/oi/funding/rsi/vol + mtf/tv/vision). It does NOT contain
        # Nexus-scanner SMC structure fields — those stay UNKNOWN by design.
        _adv_snapshot = params.get("adv_snapshot") or params.get("adv") or {}
        self.positions[symbol] = {
            "symbol": symbol, "side": side, "entry_price": fill,
            "sl_price": sl_price, "tp1_price": tp1, "tp_ladder": tp_ladder,
            "next_tp_index": 0, "initial_qty": qty,
            "qty": qty, "notional": notional, "leverage": leverage, "regime": regime,
            "opened_at": datetime.now(UTC), "status": "OPEN", "tp1_hit": False,
            "raw_entry_price": raw_fill, "entry_fee": entry_fee,
            "adv_snapshot": dict(_adv_snapshot) if isinstance(_adv_snapshot, dict) else {},
            "score": float(params.get("score", 0) or 0),
            "confidence": float(params.get("confidence", 0) or 0),
            "tag": str(params.get("tag", "")),
        }
        _persist_positions(self.positions)
        equity_after_entry = self._apply_equity_delta(-entry_fee)
        logger.info("[PAPER] OPEN %s %s @ %.6g (%s) | SL %.6g TP1 %.6g | $%.0f | fee %.4f",
                    side, symbol, fill, "limit" if raw_fill == requested_entry else "mainnet mark", sl_price, tp1, notional, entry_fee)
        return {"success": True, "ok": True, "symbol": symbol, "side": side,
                "entry_price": fill, "raw_entry_price": raw_fill, "qty": qty,
                "notional": notional, "entry_fee": entry_fee, "equity": equity_after_entry}

    # ── management ───────────────────────────────────────────────
    async def _management_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                for symbol in list(self.positions.keys()):
                    pos = self.positions.get(symbol)
                    if not pos or pos["status"] != "OPEN":
                        continue
                    mark = await self._get_mark_price(symbol)
                    if mark <= 0:
                        continue
                    side = pos["side"]; sl = pos["sl_price"]
                    hit_sl = (side == "LONG" and sl and mark <= sl) or (side == "SHORT" and sl and mark >= sl)
                    if hit_sl:
                        await self._close(symbol, sl, "HARD_SL")
                        continue
                    await self._apply_take_profits(symbol, mark)
                    pos = self.positions.get(symbol)
                    if pos:
                        self._record_stale_conflict_shadow(pos, mark)
                        self._update_shadow_trailing(pos, mark)
                        _persist_positions(self.positions)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("[PAPER] mgmt loop error: %s", e)

    async def _apply_take_profits(self, symbol: str, mark: float):
        pos = self.positions.get(symbol)
        if not pos:
            return
        ladder = pos.get("tp_ladder") or [pos.get("tp1_price")]
        idx = int(pos.get("next_tp_index", 0))
        while idx < len(ladder):
            tp = float(ladder[idx] or 0)
            hit = (pos["side"] == "LONG" and mark >= tp) or (pos["side"] == "SHORT" and mark <= tp)
            if not tp or not hit:
                break
            idx += 1
            if idx == len(ladder):
                await self._close(symbol, tp, f"TP{idx}")
                return
            slice_qty = float(pos["initial_qty"]) / len(ladder)
            pos["qty"] = max(0.0, float(pos["qty"]) - slice_qty)
            pos["next_tp_index"] = idx
            pos["tp1_hit"] = True
            _persist_positions(self.positions)
            await self._realize_partial(pos, tp, slice_qty, f"TP{idx}_PARTIAL")

    async def _realize_partial(self, pos: Dict[str, Any], exit_price: float, qty: float, reason: str):
        entry, side = pos["entry_price"], pos["side"]
        actual_exit = self._adverse_fill(exit_price, side, opening=False)
        gross_pnl = (actual_exit - entry) * qty if side == "LONG" else (entry - actual_exit) * qty
        exit_fee = self._fee(actual_exit * qty)
        pnl_usd = gross_pnl - exit_fee
        pos["baseline_partial_net_pnl"] = float(pos.get("baseline_partial_net_pnl", 0)) + pnl_usd
        pnl_pct = pnl_usd / max(entry * qty, 1e-9) * 100
        equity_after = self._apply_equity_delta(pnl_usd)
        await self._notify_close({"symbol": pos["symbol"], "side": side, "reason": reason,
            "exit_price": actual_exit, "gross_pnl_usd": gross_pnl, "fee_usd": exit_fee,
            "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
            "hold_minutes": (datetime.now(UTC)-pos["opened_at"]).total_seconds()/60,
            "equity": equity_after or 0.0, "remaining_qty": pos["qty"]})

    async def _close(self, symbol: str, exit_price: float, reason: str):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        _persist_positions(self.positions)
        entry = pos["entry_price"]; side = pos["side"]
        actual_exit = self._adverse_fill(exit_price, side, opening=False)
        notional = entry * float(pos.get("qty", 0.0))
        qty = float(pos.get("qty", 0.0))
        gross_pnl = (actual_exit - entry) * qty if side == "LONG" else (entry - actual_exit) * qty
        exit_fee = self._fee(actual_exit * qty)
        pnl_usd = gross_pnl - exit_fee
        baseline_total = float(pos.get("baseline_partial_net_pnl", 0)) + pnl_usd
        shadow_triggered = bool(pos.get("shadow_exit_price"))
        shadow_total = (float(pos.get("baseline_partial_net_pnl", 0))
                        + float(pos.get("shadow_remaining_net_pnl", 0))) if shadow_triggered else baseline_total
        pnl_pct = pnl_usd / max(notional, 1e-9) * 100
        hold_min = (datetime.now(UTC) - pos["opened_at"]).total_seconds() / 60
        logger.info("[PAPER] CLOSE %s %s @ %.6g | %s | PnL %+.2f (%+.2f%%) | hold %.1fm",
                    side, symbol, actual_exit, reason, pnl_usd, pnl_pct, hold_min)
        if self._shadow_enabled():
            try:
                _append_shadow_result({
                    "timestamp_open": pos["opened_at"], "timestamp_close": datetime.now(UTC),
                    "symbol": symbol, "side": side, "actual_reason": reason,
                    "actual_exit_price": actual_exit, "baseline_net_pnl": baseline_total,
                    "shadow_triggered": shadow_triggered,
                    "shadow_exit_price": pos.get("shadow_exit_price"),
                    "shadow_exit_at": pos.get("shadow_exit_at"),
                    "shadow_stop": pos.get("shadow_stop"),
                    "shadow_net_pnl": shadow_total,
                    "shadow_delta_usd": shadow_total - baseline_total,
                    "entry_fee_usd": pos.get("entry_fee", 0.0),
                    "trail_pct": self._shadow_trail_pct(pos),
                    "tp1_hit": bool(pos.get("tp1_hit")), "hold_minutes": hold_min,
                    "eligible_for_audit": shadow_triggered,
                })
            except Exception as e:
                logger.warning("[SHADOW_TRAIL] journal failed for %s: %s", symbol, e)
        if self._journal:
            await self._journal.write_trade({
                "timestamp_open": pos["opened_at"].isoformat(),
                "timestamp_close": datetime.now(UTC).isoformat(),
                "symbol": symbol, "side": side,
                "entry_price": entry, "exit_price": actual_exit,
                "notional_usd": notional, "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
                "gross_pnl_usd": gross_pnl, "entry_fee_usd": pos.get("entry_fee", 0.0),
                "exit_fee_usd": exit_fee,
                "hold_minutes": hold_min, "reason": reason, "raw_reason": reason,
                "normalized_reason": reason, "sl_original": pos["sl_price"],
                "active_sl_at_exit": pos["sl_price"], "sl_kind_at_exit": "ORIGINAL",
                "regime": "PAPER_MAINNET",
                # Observability: forward the enrichment captured at open so the
                # journal records WHY we entered (score/confidence + full metrics
                # blob incl. mtf/tv/vision) instead of blank UNKNOWN columns.
                "adv_snapshot": pos.get("adv_snapshot") or {},
                "score": pos.get("score", 0.0),
                "priority_score": pos.get("score", 0.0),
                "confidence": pos.get("confidence", 0.0),
                "cvd": (pos.get("adv_snapshot") or {}).get("cvd"),
                "oi_15m_pct": (pos.get("adv_snapshot") or {}).get("oi_change_15m_pct"),
                "oi_1h_pct": (pos.get("adv_snapshot") or {}).get("oi_change_1h_pct"),
                "funding_pct": (pos.get("adv_snapshot") or {}).get("funding_rate"),
                "vol_ratio": (pos.get("adv_snapshot") or {}).get("vol_ratio"),
                "rsi": (pos.get("adv_snapshot") or {}).get("rsi"),
            })

        # Feed realized PnL back into the RiskManager so equity/daily_pnl reflect
        # actual closed trades (was frozen at starting_balance before this).
        equity_after = None
        try:
            rm = self.risk_mgr
            if rm is not None and hasattr(rm, "sync_balance"):
                equity_after = self._apply_equity_delta(pnl_usd)
                if pnl_usd > 0 and hasattr(rm, "wins"):
                    rm.wins += 1
                elif pnl_usd < 0 and hasattr(rm, "losses"):
                    rm.losses += 1
        except Exception as e:
            logger.warning("[PAPER] equity sync failed for %s: %s", symbol, e)

        # Fire-and-forget close notification to the trades channel (was never sent).
        try:
            asyncio.create_task(self._notify_close({
                "symbol": symbol, "side": side, "reason": reason,
                "normalized_reason": reason, "exit_price": actual_exit,
                "gross_pnl_usd": gross_pnl, "fee_usd": exit_fee,
                "pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "hold_minutes": hold_min,
                "equity": equity_after if equity_after is not None else 0.0,
                "sl_original": pos["sl_price"], "active_sl_at_exit": pos["sl_price"],
                "sl_kind_at_exit": "ORIGINAL",
                "footer": "Fusion Quantum Dry-Run" if str(pos.get("tag", "")).startswith("fusion_quantum") else "",
            }))
        except Exception as e:
            logger.warning("[PAPER] close-notify dispatch failed for %s: %s", symbol, e)

    async def update_stop(self, symbol: str, new_sl: float) -> Dict[str, Any]:
        pos = self.positions.get(symbol)
        if not pos:
            return {"ok": False, "code": "POSITION_NOT_FOUND"}
        from signal_copy.provider_updates import safer_stop
        if not safer_stop(pos["side"], float(pos["sl_price"]), float(new_sl), float(pos["entry_price"])):
            return {"ok": False, "code": "STOP_WIDENS_RISK"}
        old = pos["sl_price"]
        pos["sl_price"] = float(new_sl)
        return {"ok": True, "code": "STOP_UPDATED", "old_sl": old, "new_sl": new_sl}

    async def close_position(self, symbol: str, reason: str = "PROVIDER_CLOSE") -> Dict[str, Any]:
        pos = self.positions.get(symbol)
        if not pos:
            return {"ok": False, "code": "POSITION_NOT_FOUND"}
        mark = await self._get_mark_price(symbol)
        if mark <= 0:
            return {"ok": False, "code": "NO_MARKET_PRICE"}
        close_reason = str(reason or "PROVIDER_CLOSE").upper()
        await self._close(symbol, mark, close_reason)
        return {"ok": True, "code": "POSITION_CLOSED", "reason": close_reason, "exit_price": mark}

    async def provider_close(self, symbol: str) -> Dict[str, Any]:
        return await self.close_position(symbol, "PROVIDER_CLOSE")

    async def _notify_close(self, payload: Dict[str, Any]) -> None:
        """Build + send a CLOSE card to the trades channel. Never raises."""
        try:
            from signal_copy.telegram_formatter import build_close_message
            from signal_copy.telegram_transport import send_trades_notification
            msg = build_close_message(payload)
            if await send_trades_notification(msg):
                return
            # Native fallback: bot image may omit python-telegram-bot.
            import json, os, urllib.request
            token = os.getenv("FQ_TELEGRAM_BOT_TOKEN") or os.getenv("SIGNAL_COPY_TRADES_NOTIFY_BOT_TOKEN") or os.getenv("SIGNAL_COPY_PARSER_NOTIFY_BOT_TOKEN")
            chat = os.getenv("FQ_TELEGRAM_CHAT_ID") or os.getenv("SIGNAL_COPY_TRADES_NOTIFY_CHAT_ID") or os.getenv("SIGNAL_COPY_PARSER_NOTIFY_CHAT_ID")
            if not token or not chat:
                raise RuntimeError("Telegram close token/chat not configured")
            body = json.dumps({"chat_id": chat, "text": "🔄 [TRADES] " + msg, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers={"Content-Type": "application/json"})
            await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
        except Exception as e:
            logger.warning("[PAPER] close notification send failed: %s", e)

    # ── introspection (RiskManager may call these) ───────────────
    def position(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p["qty"] if p and p["status"] == "OPEN" else 0.0

    def _position_symbols(self):
        return set(self.positions.keys())

    def _position_count(self) -> int:
        return len(self.positions)
