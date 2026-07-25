"""Isolated Fusion Quantum H4+15m paper runner; gateway validation only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bots.nexus_data import fetch_recent
from fusion_quantum.backtest_quantum import (
    CONFIRM_WINDOW, RR, _atr, _imbalances, _valid_obs, mss_confirm,
)
from fusion_quantum.paper_engine import submit_paper

FIB_EXPIRY = int(os.getenv("FQ_FIB_EXPIRY", "6"))
MAX_PENDING = int(os.getenv("FQ_MAX_PENDING_LIMITS", "20"))
RUNTIME = Path(os.getenv("FQ_RUNTIME_DIR", "runtime/fusion_quantum"))
STATE_PATH = Path(os.getenv("FQ_STATE_PATH", str(RUNTIME / "state.json")))
AUDIT_PATH = Path(os.getenv("FQ_PAPER_AUDIT", str(RUNTIME / "paper_audit.jsonl")))
HEARTBEAT_PATH = Path(os.getenv("FQ_HEARTBEAT_PATH", str(RUNTIME / "heartbeat.json")))
POLL_SEC = max(30, int(os.getenv("FQ_POLL_SEC", "300")))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(value, separators=(",", ":"), default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_heartbeat(path: Path, value: dict[str, Any]) -> None:
    atomic_json(path, {"updated_at": now_iso(), **value})


class State:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        except FileNotFoundError:
            self.data = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid state file {path}: {exc}") from exc
        self.data.setdefault("setups", {})

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def claim(self, setup_id: str) -> bool:
        row = self.data["setups"].get(setup_id)
        if row and row.get("status") not in {"rejected", "error"}:
            return False
        self.data["setups"][setup_id] = {"status": "confirmed", "updated_at": now_iso()}
        self.save()
        return True

    def set_status(self, setup_id: str, status: str, **extra: Any) -> None:
        row = self.data["setups"].setdefault(setup_id, {})
        row.update(status=status, updated_at=now_iso(), **extra)
        self.save()

    def pending_count(self) -> int:
        return sum(1 for row in self.data["setups"].values() if row.get("status") == "pending")

    def can_add_pending(self) -> bool:
        return self.pending_count() < MAX_PENDING

    def pending_for(self, symbol: str) -> list[tuple[str, dict[str, Any]]]:
        return [(sid, row["setup"]) for sid, row in self.data["setups"].items() if row.get("status") == "pending" and row.get("setup", {}).get("symbol") == symbol]


def setup_id(setup: dict[str, Any]) -> str:
    raw = "|".join(str(setup[k]) for k in ("symbol", "side", "confirmed_at", "entry_price"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def confirmed_setups(symbol: str, htf: pd.DataFrame, ltf: pd.DataFrame) -> list[dict[str, Any]]:
    """Reproduce backtest confirmation, entry, SL and TP formulas."""
    if len(ltf) < 10:
        return []
    atr = _atr(ltf)
    lows, highs = ltf.low.to_numpy(), ltf.high.to_numpy()
    found: list[dict[str, Any]] = []
    for side in ("BULL", "BEAR"):
        obs = _valid_obs(htf, side)
        for im in _imbalances(ltf, side):
            ce = int(im["ce"])
            prior = [ob for ob in obs if ob["t"] < im["t"] and im["leg_low"] <= ob["zhigh"] and im["leg_high"] >= ob["zlow"]]
            if not prior:
                continue
            entry0 = (im["leg_low"] + .618 * (im["leg_high"] - im["leg_low"])) if side == "BULL" else (im["leg_high"] - .618 * (im["leg_high"] - im["leg_low"]))
            start = next((i for i in range(ce + 1, min(ce + CONFIRM_WINDOW + 1, len(ltf))) if (lows[i] <= entry0 if side == "BULL" else highs[i] >= entry0)), None)
            if start is None:
                continue
            conf = mss_confirm(ltf, prior[-1], side, start, min(start + CONFIRM_WINDOW, len(ltf)))
            # Only emit limit setups still inside six-bar fill window. Initial
            # startup must not replay months of historical confirmations.
            if not conf or conf["i"] + FIB_EXPIRY < len(ltf) - 1 or not np.isfinite(atr[conf["i"]]):
                continue
            lo, hi = ((conf["sweep"], conf["disp"]) if side == "BULL" else (conf["disp"], conf["sweep"]))
            entry = (lo + .559 * (hi - lo)) if side == "BULL" else (hi - .559 * (hi - lo))
            risk = (entry - (lo - .5 * atr[conf["i"]])) if side == "BULL" else ((hi + .5 * atr[conf["i"]]) - entry)
            if risk <= 0:
                continue
            found.append({
                "symbol": symbol, "side": "LONG" if side == "BULL" else "SHORT",
                "entry_price": float(entry), "sl_price": float(entry - risk if side == "BULL" else entry + risk),
                "tp_price": float(entry + RR * risk if side == "BULL" else entry - RR * risk),
                "confirmed_at": str(ltf.open_time.iloc[conf["i"]]),
                "expires_at": str(pd.Timestamp(ltf.open_time.iloc[conf["i"]]) + pd.Timedelta(minutes=15 * FIB_EXPIRY)),
            })
    return found


def gateway_accepted(result: dict[str, Any]) -> bool:
    return bool(result.get("ok") and result.get("would_deploy") is True and result.get("status") == "paper_opened")


def pending_action(setup: dict[str, Any], low: float, high: float, at: pd.Timestamp) -> str:
    if at > pd.Timestamp(setup["expires_at"]):
        return "expired"
    entry = float(setup["entry_price"])
    if setup["side"] == "LONG":
        return "fill" if low <= entry else "wait"
    return "fill" if high >= entry else "wait"


def notify(text: str) -> None:
    token, chat = os.getenv("FQ_TELEGRAM_BOT_TOKEN"), os.getenv("FQ_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10):
        pass


def setup_message(setup: dict[str, Any]) -> str:
    side = "BUY" if setup["side"] == "LONG" else "SELL"
    symbol = setup["symbol"]

    rr = abs((setup["tp_price"] - setup["entry_price"]) / (setup["entry_price"] - setup["sl_price"]))
    tv = f"https://www.tradingview.com/chart/?symbol={symbol}.P"
    return (
        f"🆕 SETUP H4 {side} {symbol} [fusion_quantum_h4_15m]\n"
        f"Entry {setup['entry_price']:.12g} | SL {setup['sl_price']:.12g} | TP {setup['tp_price']:.12g} | RR {rr:.1f}\n"
        f"[PAPER] LIMIT placed\n"
        f"☁️ TradingView: {tv}"
    )


def fill_message(setup: dict[str, Any], result: dict[str, Any]) -> str:
    side = "BUY" if setup["side"] == "LONG" else "SELL"
    tr = ((result.get("execution") or {}).get("trader_response") or {})
    fill = tr.get("entry_price", setup["entry_price"])
    return (
        f"✅ FILLED {setup['symbol']} {side} @~{float(fill):.12g} "
        f"[fusion_quantum_h4_15m] → software SL/TP "
        f"(SL {setup['sl_price']:.12g} / TP {setup['tp_price']:.12g})"
    )


def symbols() -> list[str]:
    raw = os.getenv("FQ_SYMBOLS", "")
    if raw:
        return [x.strip().upper() for x in raw.split(",") if x.strip()]
    path = Path(os.getenv("FQ_UNIVERSE_FILE", "runtime/revo/canonical_universe.txt"))
    return [x.strip().upper() for x in path.read_text().splitlines() if x.strip()]


async def scan_once(state: State) -> dict[str, int]:
    counts = {"symbols": 0, "confirmed": 0, "pending": state.pending_count(), "filled": 0, "submitted": 0, "expired": 0, "errors": 0}
    for symbol in symbols():
        try:
            htf, ltf = await asyncio.gather(
                asyncio.to_thread(fetch_recent, symbol, "4h", 300),
                asyncio.to_thread(fetch_recent, symbol, "15m", 1000),
            )
            counts["symbols"] += 1
            if htf.attrs.get("stale") or ltf.attrs.get("stale"):
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "stale", "symbol": symbol})
                continue
            last = ltf.iloc[-1]
            for sid, setup in state.pending_for(symbol):
                action = pending_action(setup, float(last.low), float(last.high), pd.Timestamp(last.open_time))
                if action == "wait":
                    continue
                if action == "expired":
                    counts["expired"] += 1
                    state.set_status(sid, "expired", setup=setup)
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "expired", "setup_id": sid, "setup": setup})
                    continue
                result = await submit_paper(setup)
                status = "paper_opened" if gateway_accepted(result) else "rejected"
                state.set_status(sid, status, setup=setup, result=result)
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": status, "setup_id": sid, "setup": setup, "result": result})
                if status == "paper_opened":
                    counts["filled"] += 1
                    counts["submitted"] += 1
                    try:
                        await asyncio.to_thread(notify, fill_message(setup, result))
                    except Exception as exc:
                        append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "telegram_error", "error": repr(exc)})
            for setup in confirmed_setups(symbol, htf, ltf):
                sid = setup_id(setup)
                if not state.claim(sid):
                    continue
                counts["confirmed"] += 1
                if not state.can_add_pending():
                    state.set_status(sid, "rejected", setup=setup, reason="pending limit cap reached")
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "rejected", "setup_id": sid, "setup": setup, "reason": "pending limit cap reached"})
                    continue
                state.set_status(sid, "pending", setup=setup)
                counts["pending"] += 1
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "pending", "setup_id": sid, "setup": setup})
                try:
                    await asyncio.to_thread(notify, setup_message(setup))
                except Exception as exc:
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "telegram_error", "error": repr(exc)})
        except Exception as exc:
            counts["errors"] += 1
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "scan_error", "symbol": symbol, "error": repr(exc)})
    return counts


async def run() -> None:
    state = State()
    print(f"[{now_iso()}] Fusion Quantum paper runner started poll_sec={POLL_SEC}", flush=True)
    while True:
        started = time.monotonic()
        counts = await scan_once(state)
        status = "ok" if not counts["errors"] else "degraded"
        write_heartbeat(HEARTBEAT_PATH, {"status": status, **counts})
        print(
            f"[{now_iso()}] cycle status={status} symbols={counts['symbols']} "
            f"confirmed={counts['confirmed']} pending={counts['pending']} filled={counts['filled']} expired={counts['expired']} submitted={counts['submitted']} errors={counts['errors']}",
            flush=True,
        )
        await asyncio.sleep(max(1, POLL_SEC - (time.monotonic() - started)))


if __name__ == "__main__":
    asyncio.run(run())
