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
import asyncio, logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import aiohttp

UTC = timezone.utc
logger = logging.getLogger("gateway.paper")


class PaperMainnetTrader:
    """Paper trader using mainnet prices for accurate edge validation."""

    def __init__(self, nexus_api: str = "http://fastapi:8000", poll_interval: float = 3.0):
        self.nexus_api = nexus_api.rstrip("/")
        self.poll_interval = float(poll_interval)
        self.positions: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._journal = None

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

    # ── open ─────────────────────────────────────────────────────
    async def submit_open(self, timeout_sec: float = 30.0, **params) -> Optional[Dict[str, Any]]:
        del timeout_sec
        symbol = str(params.get("symbol", ""))
        side = str(params.get("side", "")).upper()
        sl_price = float(params.get("sl", params.get("sl_price", 0)) or 0)
        tp1 = float(params.get("tp1", 0) or 0)
        tp2 = float(params.get("tp2", 0) or 0)
        tp3 = float(params.get("tp3", params.get("tp_full", 0)) or 0)
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

        # Sanity guard: reject if signal SL is on the wrong side of the fill
        # (protects against fake fills that would instant-stop).
        if side == "LONG" and sl_price >= mark:
            logger.warning("[PAPER] %s LONG SL %.6g >= mark %.6g — reject (instant-stop guard)", symbol, sl_price, mark)
            return None
        if side == "SHORT" and sl_price and sl_price <= mark:
            logger.warning("[PAPER] %s SHORT SL %.6g <= mark %.6g — reject (instant-stop guard)", symbol, sl_price, mark)
            return None

        qty = notional / mark if mark > 0 else 0
        self.positions[symbol] = {
            "symbol": symbol, "side": side, "entry_price": mark,
            "sl_price": sl_price, "tp1_price": tp1, "tp2_price": tp2, "tp3_price": tp3,
            "qty": qty, "notional": notional, "leverage": leverage, "regime": regime,
            "opened_at": datetime.now(UTC), "status": "OPEN", "tp1_hit": False,
        }
        logger.info("[PAPER] OPEN %s %s @ %.6g (mainnet mark) | SL %.6g TP1 %.6g | $%.0f",
                    side, symbol, mark, sl_price, tp1, notional)
        return {"success": True, "ok": True, "symbol": symbol, "side": side,
                "entry_price": mark, "qty": qty, "notional": notional}

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
                    side = pos["side"]; sl = pos["sl_price"]; tp1 = pos["tp1_price"]
                    hit_sl = (side == "LONG" and sl and mark <= sl) or (side == "SHORT" and sl and mark >= sl)
                    if hit_sl:
                        await self._close(symbol, sl, "HARD_SL")
                        continue
                    hit_tp = (side == "LONG" and tp1 and mark >= tp1) or (side == "SHORT" and tp1 and mark <= tp1)
                    if hit_tp:
                        await self._close(symbol, tp1, "TP1")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("[PAPER] mgmt loop error: %s", e)

    async def _close(self, symbol: str, exit_price: float, reason: str):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        entry = pos["entry_price"]; side = pos["side"]; notional = pos["notional"]
        pnl_pct = ((exit_price - entry) / entry * 100) if side == "LONG" else ((entry - exit_price) / entry * 100)
        pnl_usd = notional * pnl_pct / 100
        hold_min = (datetime.now(UTC) - pos["opened_at"]).total_seconds() / 60
        logger.info("[PAPER] CLOSE %s %s @ %.6g | %s | PnL %+.2f (%+.2f%%) | hold %.1fm",
                    side, symbol, exit_price, reason, pnl_usd, pnl_pct, hold_min)
        if self._journal:
            await self._journal.write_trade({
                "timestamp_open": pos["opened_at"].isoformat(),
                "timestamp_close": datetime.now(UTC).isoformat(),
                "symbol": symbol, "side": side,
                "entry_price": entry, "exit_price": exit_price,
                "notional_usd": notional, "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
                "hold_minutes": hold_min, "reason": reason, "raw_reason": reason,
                "normalized_reason": reason, "sl_original": pos["sl_price"],
                "active_sl_at_exit": pos["sl_price"], "sl_kind_at_exit": "ORIGINAL",
                "regime": "PAPER_MAINNET",
            })

    # ── introspection (RiskManager may call these) ───────────────
    def position(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p["qty"] if p and p["status"] == "OPEN" else 0.0

    def _position_symbols(self):
        return set(self.positions.keys())

    def _position_count(self) -> int:
        return len(self.positions)
