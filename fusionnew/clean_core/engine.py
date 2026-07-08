"""Clean-core live engine for the validated IMBALANCE strategy.

Pipeline (single hard gate): scan universe -> generate_setups (EMA50/200 + OB + imbalance + fib)
-> optional OI/CVD/funding filter -> risk sizing -> testnet LIMIT entry + reduce-only SL/TP
-> lifecycle management.

Safety:
 * --dry (default): no real orders; fills simulated from klines (paper) to exercise the lifecycle.
 * --live --arm : actually place orders on Binance testnet. Without --arm, --live stays dry.
State persisted to clean_core/state/engine_state.json. Telegram alerts via config creds.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("LOG_DIR", "/tmp/bt_logs")

import requests

from backtest.data import fetch_recent
from backtest.faithful_imbalance import (
    FIB_EXPIRY, TIERS, _filter_ema_dist, _filter_flow, _filter_liquidity, _filter_stochastic,
    _trend, _trend_ok, recent_setups, generate_setups,
)
from clean_core.executor import FuturesTestnet

try:
    from config import TELEGRAMBOTTOKEN, TELEGRAM_CHAT_ID
except Exception:
    TELEGRAMBOTTOKEN = TELEGRAM_CHAT_ID = None

LTF_MIN = {"15m": 15, "5m": 5, "3m": 3}
STATE_DIR = Path(os.getenv("STATE_DIR", str(Path(__file__).parent / "state")))
STATE_FILE_TEMPLATE = "engine_state_{tier}_{direction}.json"

def _utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()

def tg(text: str) -> None:
    if not TELEGRAMBOTTOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAMBOTTOKEN}/sendMessage",
            json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text}, timeout=10)
    except Exception:
        pass


# ------------- lOGGING UTILITIES ---------------
def _flog(self, event: str, t: Dict[str, Any], extra: Dict[str, Any] = None) -> None:
    """Append a forward record (compare with backtest + measure OI later)."""
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event, **t}
    if extra:
        rec.update(extra)
    try:
        with (self._flog_path()).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass

def _oi_snapshot(self, symbol: str) -> Dict[str, Any]:
    """Capture OI now + recent change (OI can't be backtested -> measured forward)."""
    try:
        from backtest.data import fetch_oi
        oi = fetch_oi(symbol, "5m", 30)
        if oi is None or len(oi) < 6:
            return {}
        arr = oi["oi"].to_numpy()
        now, prev = float(arr[-1]), float(arr[-6])
        chg = (now - prev) / prev if prev else 0.0
        return {"oi_now": now, "oi_chg6": round(chg, 5)}
    except Exception:
        return {}

class RiskGuard:
    """Clean circuit breaker: % risk/trade on FIXED equity_ref (shared account),
    notional cap, max concurrent, UTC daily-loss reset, max-drawdown PERMANENT halt."""

    def __init__(self, equity_ref: float, risk_pct: float, max_positions: int,
                 max_notional_mult: float, daily_loss_limit_pct: float, max_dd_pct: float = 0.20):
        self.equity_ref = equity_ref
        self.risk_pct = risk_pct
        self.max_positions = max_positions
        self.max_notional = equity_ref * max_notional_mult
        self.daily_loss_limit = equity_ref * daily_loss_limit_pct
        self.max_dd = equity_ref * max_dd_pct
        self.realized_today = 0.0
        self.realized_total = 0.0
        self.peak_equity = equity_ref
        self.halted_permanent = False
        self.day = _utc_day()

    def roll_day(self) -> None:
        today = _utc_day()
        if today != self.day:
            self.day = today
            self.realized_today = 0.0

    def record(self, pnl: float) -> None:
        self.realized_today += pnl
        self.realized_total += pnl
        eq = self.equity_ref + self.realized_total
        self.peak_equity = max(self.peak_equity, eq)
        if self.peak_equity - eq >= self.max_dd:
            self.halted_permanent = True

    def qty_for(self, entry: float, sl: float) -> float:
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return 0.0
        qty = (self.equity_ref * self.risk_pct) / risk_per_unit
        if qty * entry > self.max_notional:  # cap notional
            qty = self.max_notional / entry
        return qty

    def block_reason(self, n_open: int) -> str:
        if self.halted_permanent:
            return "MAX_DRAWDOWN_HALT"
        if n_open >= self.max_positions:
            return "MAX_POSITIONS"
        if self.realized_today <= -self.daily_loss_limit:
            return "DAILY_LOSS_LIMIT"
        return ""

    def can_open(self, n_open: int) -> bool:
        return self.block_reason(n_open) == ""

    def to_dict(self) -> Dict[str, Any]:
        return {"realized_today": self.realized_today, "realized_total": self.realized_total,
                "peak_equity": self.peak_equity, "halted_permanent": self.halted_permanent,
                "day": self.day}

    def load_dict(self, d: Dict[str, Any]) -> None:
        self.realized_today = d.get("realized_today", 0.0)
        self.realized_total = d.get("realized_total", 0.0)
        self.peak_equity = d.get("peak_equity", self.equity_ref)
        self.halted_permanent = d.get("halted_permanent", False)
        self.day = d.get("day", _utc_day())


# -------------- ENGINE --------------
class Engine:
    def _path(self) -> Path:
        base = STATE_FILE_TEMPLATE.format(tier=self.tier, direction=self.direction)
        if self.tag:
            base = base.replace(".json", f"{self.tag}.json")
        return STATE_DIR / base

    def _flog_path(self) -> Path:
        base = f"forward_trades_{self.tier}_{self.direction}"
        if self.tag:
            base += self.tag
        return STATE_DIR / f"{base}.jsonl"

    def __init__(self, ex: FuturesTestnet, risk: RiskGuard, tier: str, rr: float,
                 leverage: int, dry: bool, use_cvd: bool = False, use_btc: bool = False,
                 tag: str = "", ema_dist: float = 0.0, min_turn: float = 0.0, sl_swing: int = 0,
                 direction: str = "both", warm_batch: int = 40, cold_batch: int = 25,
                 soft_pending_cap: int = 20, stoch_max: float = 0.0):
        self.ex = ex
        self.risk = risk
        self.tier = tier
        self.rr = rr
        self.leverage = leverage
        self.dry = dry
        self.use_cvd = use_cvd
        self.use_btc = use_btc
        self.use_adversarial = False  # off by default; enable via --adversarial
        self.tag = tag
        self.ema_dist = ema_dist
        self.min_turn = min_turn
        self.sl_swing = sl_swing
        self.direction = direction
        self.warm_batch = warm_batch
        self.cold_batch = cold_batch
        self.soft_pending_cap = soft_pending_cap
        self.stoch_max = stoch_max
        self._btc_trend = None
        self._stats = {"cand": 0, "stale": 0, "fresh": 0, "blocked": 0,
                       "blocked_max_open": 0, "blocked_pending_cap": 0, "min_age": 9999}
        self.trades: List[Dict[str, Any]] = []
        self.closed: List[Dict[str, Any]] = []
        self._seen: set = set()
        self._cooldowns: dict = {}       # symbol -> {ts, reason} cooldown after close
        # --- Adaptive scan tracking ---
        self._symbol_last_scan = {}  # Last scanned UTC EPOCH
        self._symbol_nearest = {}    # Min candlestick age at last scan
        self._scan_tick = 0          # Monotonic cycle counter (for stride decisions)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._load()
        # Re-attach injected utility methods
        self._flog = _flog.__get__(self)
        self._oi_snapshot = _oi_snapshot.__get__(self)

    def _load(self) -> None:
        p = self._path()
        if p.exists():
            try:
                d = json.loads(p.read_text())
                self.trades = d.get("trades", [])
                self.closed = d.get("closed", [])
                self._seen = set(tuple(x) for x in d.get("seen", []))
                if "risk" in d:
                    self.risk.load_dict(d["risk"])
                # Restore scan tracking if existing
                self._symbol_last_scan = d.get("_symbol_last_scan", {})
                self._symbol_nearest = d.get("_symbol_nearest", {})
            except Exception as exc:
                print(f"[WARN] state {p} load failed: {exc}")

    def _save(self) -> None:
        try:
            self._path().write_text(json.dumps({
                "trades": self.trades,
                "closed": self.closed,
                "seen": [list(x) for x in self._seen],
                "risk": self.risk.to_dict(),
                "_symbol_last_scan": {k: round(v, 2) for k, v in self._symbol_last_scan.items()},
                "_symbol_nearest": self._symbol_nearest,
            }, default=str, indent=2))
        except Exception as exc:
            print(f"[WARN] state save failed: {exc}")

    def _has_symbol(self, symbol: str) -> bool:
        return any(t["symbol"] == symbol for t in self.trades)

    # --- COOLDOWN: prevent re-entry after SL/TP ---
    COOLDOWN_MINUTES = 120  # 2 hour cooldown after any close

    def _in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in post-close cooldown."""
        cooldown_data = self._cooldowns.get(symbol)
        if not cooldown_data:
            return False
        elapsed = (time.time() - cooldown_data["ts"]) / 60.0
        return elapsed < self.COOLDOWN_MINUTES

    def _add_cooldown(self, symbol: str, reason: str) -> None:
        """Record cooldown start for a symbol."""
        self._cooldowns[symbol] = {"ts": time.time(), "reason": reason}

    def scan_symbol(self, symbol: str, ltf_recent: int = 1000) -> None:
        """Adaptive: records nearest age, updates scan trackers."""
        # COOLDOWN GUARD: skip if symbol recently closed
        if self._in_cooldown(symbol):
            return
        cfg = TIERS[self.tier]
        zone_df = fetch_recent(symbol, cfg["zone"], 300)
        ltf = fetch_recent(symbol, cfg["ltf"], ltf_recent).iloc[:-1]
        if min(len(zone_df), len(ltf)) < 260:
            self._symbol_nearest[symbol] = 9999
            return
        trend = _trend(zone_df)
        bull_raw = generate_setups(zone_df, ltf, trend, "BULL", self.rr, sl_swing=self.sl_swing) \
            if self.direction in ("both", "long") else []
        bear_raw = generate_setups(zone_df, ltf, trend, "BEAR", self.rr, sl_swing=self.sl_swing) \
            if self.direction in ("both", "short") else []
        # ponytail: skip max_age — market slow, fresh setups rare
        bull = bull_raw
        bear = bear_raw
        if self.use_cvd:
            bull = _filter_flow(symbol, 0, "BULL", bull, ltf, True, False)
            bear = _filter_flow(symbol, 0, "BEAR", bear, ltf, True, False)
        if self.use_btc and self._btc_trend is not None:
            bull = [s for s in bull if _trend_ok(self._btc_trend, s["t_complete"], "BULL")]
            bear = [s for s in bear if _trend_ok(self._btc_trend, s["t_complete"], "BEAR")]
        if self.ema_dist > 0:
            bull = _filter_ema_dist(bull, zone_df, self.ema_dist)
            bear = _filter_ema_dist(bear, zone_df, self.ema_dist)
        if self.min_turn > 0:
            bull = _filter_liquidity(bull, ltf, self.min_turn)
            bear = _filter_liquidity(bear, ltf, self.min_turn)
        if self.stoch_max > 0:
            bull = _filter_stochastic(bull, ltf, "BULL", self.stoch_max)
            bear = _filter_stochastic(bear, ltf, "BEAR", self.stoch_max)
        alls = bull + bear
        n = len(ltf)
        nearest_age = 9999
        for s in alls:
            age = n - 1 - int(s["ce"])
            nearest_age = min(nearest_age, age)
        self._stats["min_age"] = min(self._stats["min_age"], nearest_age)
        fresh = [s for s in alls if (n - 1 - int(s["ce"])) <= 288]  # WIDE, match backtest max_age
        self._stats["cand"] += len(fresh)
        # Update scan metadata
        self._symbol_last_scan[symbol] = time.time()
        self._symbol_nearest[symbol] = nearest_age
        if not fresh:
            return
        fresh.sort(key=lambda s: s["t_complete"])
        s = fresh[-1]
        key = (symbol, s["side"], str(s["t_complete"]))
        if key in self._seen:
            return
        self._stats["fresh"] += 1
        self._seen.add(key)
        n_open = sum(1 for t in self.trades if t.get("status") == "OPEN")
        n_pending = sum(1 for t in self.trades if t.get("status") == "PENDING")
        if self._has_symbol(symbol) or self._busy_on_exchange(symbol):
            self._stats["blocked"] += 1
            return
        if n_open >= self.risk.max_positions:
            self._stats["blocked"] += 1
            self._stats["blocked_max_open"] = self._stats.get("blocked_max_open", 0) + 1
            return
        if n_pending >= self.soft_pending_cap:
            self._stats["blocked"] += 1
            self._stats["blocked_pending_cap"] = self._stats.get("blocked_pending_cap", 0) + 1
            return
        if not self.risk.can_open(n_open):
            self._stats["blocked"] += 1
            return
        # Adversarial check — 12-agent pipeline with model pool round-robin (v2)
        if self.use_adversarial:
            v2_enabled = os.getenv("ADVERSARIAL_VERSION", "v1") == "v2"
            if v2_enabled:
                from clean_core.adversarial_v2 import adversarial_check_v2
                ltf_row = ltf.iloc[-1] if ltf is not None and len(ltf) > 0 else {}
                context = {
                    "current_price": float(ltf_row.get("close", s.get("entry", 0))),
                    "volume": float(ltf_row.get("volume", 0)),
                    "turnover": float(ltf_row.get("turnover", 0)),
                    "qvol": float(ltf_row.get("quote_volume", float(ltf_row.get("volume", 0)) * float(ltf_row.get("close", s.get("entry", 0))))),
                    "spread": 0.02,
                    "depth": "normal",
                    "vol_spike": "none",
                    "news_flag": "none",
                    "rsi": 50,
                    "ema_dist": 0.0,
                    "atr": 3.0,
                    "chop": 0.5,
                    "er": 0.3,
                    "cvd_z": 0.0,
                    "oi_delta": 0.0,
                    "funding": 0.0,
                    "flow_verdict": "neutral",
                    "btc_regime": "neutral",
                    "btc_dom": 55,
                    "dxy": 100,
                    "vix": 15,
                    "ls_ratio": 1.0,
                    "fng": 50,
                    "bid": float(ltf_row.get("close", s.get("entry", 0))) * 0.999,
                    "ask": float(ltf_row.get("close", s.get("entry", 0))) * 1.001,
                    "time_to_close": 300,
                    "equity": self.risk.equity_ref,
                    "risk_pct": self.risk.risk_pct * 100,
                    "cur_pos": n_open,
                    "max_positions": self.risk.max_positions,
                    "daily_pnl": 0.0,
                    "max_dd": self.risk.max_dd / self.risk.equity_ref * 100 if self.risk.equity_ref else 20.0,
                }
                s["tier"] = self.tier
                s["direction"] = "LONG" if s.get("side") == "BULL" else "SHORT"
                print(f"🤖 [ADVv2] Checking {symbol} ({s.get('direction','?')}) with 12-agent pipeline...")
                ok, reason, journal = adversarial_check_v2(symbol, s, context)
                print(f"🤖 [ADVv2] {symbol}: {'APPROVED' if ok else 'REJECTED'} — {reason}")
                if not ok:
                    self._stats["blocked"] += 1
                    self._stats["blocked_adversarial"] = self._stats.get("blocked_adversarial", 0) + 1
                    self._flog("ADVv2_REJECT", dict(symbol=symbol, reason=reason, scores=journal.get("agents", {})))
                    return
                self._flog("ADVv2_APPROVED", dict(symbol=symbol, reason=reason, journal=journal.get("summary", "")))
            else:
                from clean_core.adversarial import bull_bear_check
                print(f"🤖 [ADV] Checking {symbol} {s.get('direction','?')}...")
                ok, reason = bull_bear_check(symbol, s)
                print(f"🤖 [ADV] {symbol}: {'BULL_WINS' if ok else 'BEAR_WINS'} — {reason}")
                if not ok:
                    self._stats["blocked"] += 1
                    self._stats["blocked_adversarial"] = self._stats.get("blocked_adversarial", 0) + 1
                    self._flog("BEAR_WINS", dict(symbol=symbol, reason=reason))
                    return
        self._open_pending(symbol, s)

    def _busy_on_exchange(self, symbol: str) -> bool:
        """Live guard: skip if the account already has a position/order on this symbol."""
        if self.dry:
            return False
        try:
            if abs(self.ex.position(symbol)) > 0 or self.ex.open_orders(symbol):
                return True
        except Exception:
            return False
        return False

    def _open_pending(self, symbol: str, s: Dict[str, Any]) -> None:
        side = "BUY" if s["side"] == "BULL" else "SELL"
        entry, sl, tp = s["entry"], s["sl"], s["tp"]
        qty = self.ex.round_qty(symbol, self.risk.qty_for(entry, sl))
        if qty <= 0:
            return
        self.ex.set_leverage(symbol, self.leverage)
        self.ex.limit_entry(symbol, side, qty, entry)
        step = LTF_MIN[TIERS[self.tier]["ltf"]]
        rec = {
            "symbol": symbol, "tier": self.tier, "side": side, "imb_side": s["side"],
            "entry": entry, "sl": sl, "tp": tp, "qty": qty, "status": "PENDING",
            "t_complete": str(s["t_complete"]),
            "expiry_min": FIB_EXPIRY * step, "opened_at": time.time()
        }
        rec.update(self._oi_snapshot(symbol))
        self.trades.append(rec)
        self._flog("SETUP", rec)
        # TradingView symbol = {symbol}USDT.P or {symbol}USDTP (prefer {symbol}USDT.P)
        tv_symbol = symbol + "USDT.P" if not symbol.endswith("USDT") else symbol + ".P"
        msg = (
            f"🆕 SETUP {self.tier} {side} {symbol}\n"
            f"Entry {entry:.6g} | SL {sl:.6g} | TP {tp:.6g} | qty {qty} | RR {self.rr}\n"
            f"{'[DRY] ' if self.dry else ''}LIMIT placed\n"
            f"☁️ TradingView: https://www.tradingview.com/chart/?symbol={tv_symbol}"
        )
        print(msg)
        tg(msg)

    def _filled(self, t: Dict[str, Any], symbol: str, hi: float, lo: float) -> bool:
        if self.dry:  # simulate fill from klines
            return lo <= t["entry"] if t["side"] == "BUY" else hi >= t["entry"]
        return abs(self.ex.position(symbol)) > 0  # real: position opened

    def _manage_pending(self, t: Dict[str, Any], symbol: str, hi: float, lo: float) -> None:
        age_min = (time.time() - t["opened_at"]) / 60.0
        if self._filled(t, symbol, hi, lo):
            t["status"] = "OPEN"
            tp_note = "software SL/TP"
            if not self.dry:  # HARDWARE SL+TP via Algo Order API
                try:
                    pos = abs(self.ex.position(symbol))
                    if pos > 0:
                        cs = "SELL" if t["side"] == "BUY" else "BUY"
                        self.ex.algo_conditional(symbol, cs, "STOP_MARKET", t["sl"], pos)
                        self.ex.algo_conditional(symbol, cs, "TAKE_PROFIT_MARKET", t["tp"], pos)
                        tp_note = "HARDWARE SL+TP armed"
                except Exception as exc:
                    print(f" algo SL/TP place err {symbol}: {exc}")
            msg = (f"✅ FILLED {symbol} {t['side']} @~{t['entry']:.6g} → "
                   f"{tp_note} (SL {t['sl']:.6g} / TP {t['tp']:.6g})")
            print(msg)
            tg(msg)
            self._flog("FILL", t)
        elif age_min > t["expiry_min"]:
            self.ex.cancel_all(symbol)
            t["status"] = "CANCELLED"
            print(f"⌛ EXPIRED unfilled {symbol} {t['side']} — cancelled")

    def _manage_open(self, t: Dict[str, Any], symbol: str, hi: float, lo: float, close: float) -> None:
        exit_price = reason = None
        if self.dry:
            if t["side"] == "BUY":
                if lo <= t["sl"]:
                    exit_price, reason = t["sl"], "SL"
                elif hi >= t["tp"]:
                    exit_price, reason = t["tp"], "TP"
            else:
                if hi >= t["sl"]:
                    exit_price, reason = t["sl"], "SL"
                elif lo <= t["tp"]:
                    exit_price, reason = t["tp"], "TP"
        else:
            # LIVE: hardware SL+TP (algo orders) handle exits
            if abs(self.ex.position(symbol)) == 0:
                if t["side"] == "BUY":
                    reason = "TP" if close >= t["entry"] else "SL"
                else:
                    reason = "TP" if close <= t["entry"] else "SL"
                exit_price = t["tp"] if reason == "TP" else t["sl"]
                self.ex.cancel_algo(symbol)
        if exit_price is None:
            return
        gross = (exit_price - t["entry"]) if t["side"] == "BUY" else (t["entry"] - exit_price)
        from backtest.faithful_imbalance import MAKER_FEE, SLIP, TAKER_FEE
        fees = t["entry"] * MAKER_FEE + exit_price * (TAKER_FEE + SLIP)
        pnl = t["qty"] * (gross - fees)
        self.risk.record(pnl)
        t.update({"status": "CLOSED", "exit": exit_price, "reason": reason, "pnl": pnl})
        self.closed.append(t)
        # COOLDOWN: prevent immediate re-entry after close
        self._add_cooldown(symbol, reason or "UNKNOWN")
        msg = f"{'🟢' if pnl > 0 else '🔴'} CLOSED {symbol} {reason} pnl={pnl:+.4f}"
        print(msg)
        tg(msg)
        self._flog("CLOSE", t)

    def manage(self, symbol: str) -> None:
        """Always run on every cycle for all symbols."""
        cfg = TIERS[self.tier]
        # Use 1m bars for fill detection (more sensitive to wicks), not 5m
        ltf = fetch_recent(symbol, "1m", 100).iloc[:-1]  # only closed bars
        if ltf.empty:
            return
        bar = ltf.iloc[-1]
        hi, lo, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        for t in [x for x in self.trades if x["symbol"] == symbol]:
            if t["status"] == "PENDING":
                self._manage_pending(t, symbol, hi, lo)
            elif t["status"] == "OPEN":
                self._manage_open(t, symbol, hi, lo, close)

    def cycle(self, symbols: List[str]) -> None:
        """Adaptive scan: prioritize symbols with setup proximity."""
        self.risk.roll_day()
        self._stats = {"cand": 0, "stale": 0, "fresh": 0, "blocked": 0,
                       "blocked_max_open": 0, "blocked_pending_cap": 0, "min_age": 9999}
        self._scan_tick += 1
        if self.use_btc:
            try:
                self._btc_trend = _trend(fetch_recent("BTCUSDT", TIERS[self.tier]["zone"], 300))
            except Exception:
                self._btc_trend = None
        halt = self.risk.block_reason(sum(1 for t in self.trades if t["status"] == "OPEN"))
        active_symbols = []
        for t in self.trades:
            if t.get("status") in ("PENDING", "OPEN") and t.get("symbol") not in active_symbols:
                active_symbols.append(t["symbol"])

        hot_set, warm_set, cold_set = [], [], []
        for sym in symbols:
            nearest = self._symbol_nearest.get(sym, 9999)
            last_scan = self._symbol_last_scan.get(sym, 0)
            now = time.time()
            last_age_sec = now - last_scan
            self._stats["min_age"] = min(self._stats["min_age"], nearest)
            if sym in active_symbols:
                hot_set.append(sym)  # PENDING/OPEN is always HOT
            elif nearest <= 20:  # <= 20 bars = HOT (real-time)
                hot_set.append(sym)
            elif nearest <= 100 and last_age_sec < 3600:
                warm_set.append(sym)
            else:
                cold_set.append(sym)

        def _rr_batch(items: List[str], size: int, salt: int = 0) -> List[str]:
            if size <= 0 or not items:
                return []
            if len(items) <= size:
                return items
            start = ((self._scan_tick + salt) * size) % len(items)
            return [items[(start + i) % len(items)] for i in range(size)]

        # Scan strategy
        scan_order = list(hot_set)
        # Warm symbols on every 5th cycle, capped to prevent bursts.
        if self._scan_tick % 5 == 0:
            scan_order += _rr_batch(warm_set, self.warm_batch, salt=7)
        # COLD stays alive via small round-robin batches, not full-universe bursts.
        cold_batch = _rr_batch(cold_set, self.cold_batch, salt=17)
        scan_order += cold_batch

        # Deduplicate while preserving priority order: active/HOT -> WARM -> COLD batch.
        seen_scan = set(active_symbols)
        scan_order = [s for s in scan_order if not (s in seen_scan or seen_scan.add(s))]

        print(
            f"[ADAPTIVE] HOT {len(hot_set)} | WARM {len(warm_set)} | COLD {len(cold_set)} "
            f"| scan={len(scan_order)} cold_batch={len(cold_batch)} active={len(active_symbols)}"
        )
        # Always manage active PENDING/OPEN first, even if symbol is outside the scheduled scan batch.
        for sym in active_symbols:
            try:
                self.manage(sym)
            except Exception as exc:
                print(f" {sym} manage err: {exc}")
        for sym in scan_order:
            try:
                self.manage(sym)
                if not halt:
                    self.scan_symbol(sym)
            except Exception as exc:
                print(f" {sym} err: {exc}")

        # Cleanup; persistance
        self.trades = [t for t in self.trades if t["status"] in ("PENDING", "OPEN")]
        self._save()

    # ---- reporting ----
    def stats_report(self) -> str:
        """Used in print and TG."""
        n_open = sum(1 for t in self.trades if t["status"] == "OPEN")
        n_pend = sum(1 for t in self.trades if t["status"] == "PENDING")
        wins = sum(1 for c in self.closed if c.get("pnl", 0) > 0)
        net = sum(c.get("pnl", 0) for c in self.closed)
        flag = f" HALT={self.risk.block_reason(n_open)}" if self.risk.block_reason(n_open) else ""
        st = self._stats
        return (
            f"[{self.tier}{self.tag}] open={n_open} pending={n_pend} "
            f"closed={len(self.closed)} wins={wins} net={net:+.4f} "
            f"today={self.risk.realized_today:+.4f} | "
            f"cand={st['cand']} fresh={st['fresh']} blk={st['blocked']}(O:{st.get('blocked_max_open',0)},P:{st.get('blocked_pending_cap',0)}) "
            f"nearest={st['min_age'] if st['min_age'] < 9999 else '-'}bars{flag}"
        )

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["H4", "H1", "M15", "M30"], default="H4")
    ap.add_argument("--symbols", nargs="+",
                    default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"])
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--risk-pct", type=float, default=0.01)
    ap.add_argument("--max-positions", type=int, default=4)
    ap.add_argument("--max-notional-mult", type=float, default=3.0)
    ap.add_argument("--daily-loss-pct", type=float, default=0.05)
    ap.add_argument("--max-dd-pct", type=float, default=0.20)
    ap.add_argument("--leverage", type=int, default=5)
    ap.add_argument("--interval-sec", type=int, default=120)
    ap.add_argument("--cycles", type=int, default=0)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--arm", action="store_true", help="actually send real orders (requires --live)")
    ap.add_argument("--cvd", action="store_true", help="require CVD confirmation (recommended for H1)")
    ap.add_argument("--btc-regime", action="store_true", help="only trade in BTC's macro-regime direction")
    ap.add_argument("--adversarial", action="store_true", help="run bull vs bear LLM debate before entry")
    ap.add_argument("--tag", default="", help="state/log suffix to isolate parallel instances (e.g. _paper)")
    ap.add_argument("--ema-dist", type=float, default=0.0, help="min |price-EMA200|/ATR gate (zone TF); 0=off")
    ap.add_argument("--min-turn", type=float, default=0.0, help="min recent LTF quote-turnover (20-bar); 0=off")
    ap.add_argument("--sl-swing", type=int, default=0, help="SL at swing-low over N LTF bars (0=leg SL); validated for H1")
    ap.add_argument("--warm-batch", type=int, default=40, help="max WARM symbols to heavy-scan on scheduled cycles")
    ap.add_argument("--cold-batch", type=int, default=25, help="max COLD symbols to refresh per cycle")
    ap.add_argument("--soft-pending-cap", type=int, default=20, help="max PENDING orders before blocking new setups")
    ap.add_argument("--stoch-max", type=float, default=0.0, help="Stochastic %K ceiling filter; 0=off, 50=recommended")
    ap.add_argument("--direction", choices=["both", "long", "short"], default="both", 
                   help="only trade long, short, or both (default: both)")
    a = ap.parse_args()
    
    dry = not (a.live and a.arm)
    ex = FuturesTestnet(dry=dry)
    risk = RiskGuard(a.equity, a.risk_pct, a.max_positions, a.max_notional_mult,
                     a.daily_loss_pct, a.max_dd_pct)
    eng = Engine(ex, risk, a.tier, a.rr, a.leverage, dry, 
                 use_cvd=a.cvd, use_btc=a.btc_regime, tag=a.tag,
                 ema_dist=a.ema_dist, min_turn=a.min_turn,
                 sl_swing=a.sl_swing, direction=a.direction,
                 warm_batch=a.warm_batch, cold_batch=a.cold_batch,
                 soft_pending_cap=a.soft_pending_cap, stoch_max=a.stoch_max)
    eng.use_adversarial = a.adversarial
    print(f"[DEBUG] use_adversarial={eng.use_adversarial}")
    
    mode = "LIVE-ARMED (REAL ORDERS)" if not dry else "DRY/PAPER (no real orders)"
    print(f"[ENGINE {a.tier}{a.tag}] {mode} | symbols={len(a.symbols)} | risk={a.risk_pct:.0%} "
          f"equity_ref={a.equity} lev={a.leverage} cvd={a.cvd} btc_regime={a.btc_regime} "
          f"ema_dist={a.ema_dist} min_turn={a.min_turn} sl_swing={a.sl_swing} "
          f"stoch_max={a.stoch_max} "
          f"warm_batch={a.warm_batch} cold_batch={a.cold_batch} soft_pending_cap={a.soft_pending_cap} "
          f"direction={a.direction} every {a.interval_sec}s | adversarial={'ON' if a.adversarial else 'OFF'}"
          f"{' | ADAPTIVE priority scan' if not a.adversarial else ''}")
    
    c = 0
    while True:
        eng.cycle(a.symbols)
        c += 1
        print(eng.stats_report())
        if a.cycles and c >= a.cycles:
            return
        time.sleep(a.interval_sec)


if __name__ == "__main__":
    main()