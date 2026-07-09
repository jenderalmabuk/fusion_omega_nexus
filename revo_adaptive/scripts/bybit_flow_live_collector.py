#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

API_BASE = os.environ.get("BYBIT_API_BASE", "https://api.bybit.com").rstrip("/")
CATEGORY = os.environ.get("BYBIT_CATEGORY", "linear")
REPO_DIR = Path(os.environ.get("REVO_REPO_DIR", "/home/fusion_omega/revo_adaptive"))
RUNTIME_DIR = Path(os.environ.get("REVO_RUNTIME_DIR", str(REPO_DIR / "user_data/revo_alpha/runtime/bybit")))

INTERVAL_SEC = int(float(os.environ.get("BYBIT_COLLECTOR_INTERVAL_SEC", "180")))
TOP_N = int(float(os.environ.get("BYBIT_COLLECTOR_TOP_N", "150")))
TRADE_LIMIT = int(float(os.environ.get("BYBIT_COLLECTOR_RECENT_TRADES_LIMIT", "200")))
HTTP_SLEEP_SEC = float(os.environ.get("BYBIT_HTTP_SLEEP_SEC", "0.25"))
HTTP_TIMEOUT_SEC = float(os.environ.get("BYBIT_HTTP_TIMEOUT_SEC", "15"))
MAX_SYMBOLS_PER_CYCLE = int(float(os.environ.get("BYBIT_COLLECTOR_MAX_SYMBOLS", str(TOP_N))))

OUT_JSON = RUNTIME_DIR / "revo_flow_context_collector.json"
OUT_CSV = RUNTIME_DIR / "revo_flow_context_collector.csv"
HEARTBEAT = RUNTIME_DIR / "BYBIT_FLOW_COLLECTOR_HEARTBEAT_COMPACT.txt"
HEARTBEAT_JSON = RUNTIME_DIR / "bybit_flow_collector_heartbeat_latest.json"
HEARTBEAT_JSONL = RUNTIME_DIR / "bybit_flow_collector_heartbeat.jsonl"
LOG_FILE = RUNTIME_DIR / "BYBIT_FLOW_COLLECTOR.log"

PAIRLIST_FILE = RUNTIME_DIR / "pair_universe_remote.json"



# F4X_AS5J2C_SOURCE_SELECTION_V2_ADDITIVE_CONSTANTS
# Preserves original TRADE_LIMIT / HTTP / output / heartbeat constants.
MIN_VOLUME_USD = float(os.environ.get("BYBIT_COLLECTOR_MIN_VOLUME_USD", "4000000"))
HOT_LIMIT = int(float(os.environ.get("BYBIT_COLLECTOR_HOT_LIMIT", "40")))
WARM_LIMIT = int(float(os.environ.get("BYBIT_COLLECTOR_WARM_LIMIT", "90")))
COLD_LIMIT = int(float(os.environ.get("BYBIT_COLLECTOR_COLD_LIMIT", "40")))
USE_PAIR_UNIVERSE = os.environ.get("BYBIT_COLLECTOR_USE_PAIR_UNIVERSE", "1") != "0"
USE_FEEDER_RAW = os.environ.get("BYBIT_COLLECTOR_USE_FEEDER_RAW", "1") != "0"
USE_F3_STATES = os.environ.get("BYBIT_COLLECTOR_USE_F3_STATES", "1") != "0"
FEEDER_RAW_FILE = RUNTIME_DIR / "F4X_LEGACY_FEEDER_RAW_UNIVERSE_REPORT_ONLY.json"
FEEDER_LANES_FILE = RUNTIME_DIR / "F4X_LEGACY_FEEDER_HOT_WARM_COLD_REPORT_ONLY.json"
F3A_B_FILE = RUNTIME_DIR / "revo_f3a_b_flow_cache_health_classifier_state.json"
F3B_FILE = RUNTIME_DIR / "revo_f3b_regime_aware_oi_interpreter_state.json"
FULL_FILE = RUNTIME_DIR / "F4X_FULL_CONFLUENCE_FINAL_FULL.json"
AS5_FILE = RUNTIME_DIR / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json"


class TemperatureClassifier:
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
    TIERS = (HOT, WARM, COLD)

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self.hot_every_n = self._env_int("BYBIT_TEMP_HOT_EVERY_N", 1)
        self.warm_every_n = self._env_int("BYBIT_TEMP_WARM_EVERY_N", 2)
        self.cold_every_n = self._env_int("BYBIT_TEMP_COLD_EVERY_N", 4)
        self.state: Dict[str, Any] = {"pairs": {}, "updated_at": utc_now()}
        self._load()

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return max(1, int(float(os.environ.get(name, str(default)))))
        except Exception:
            return default

    def _load(self) -> None:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    pairs = data.get("pairs")
                    self.state = data
                    if not isinstance(pairs, dict):
                        self.state["pairs"] = {}
        except Exception:
            self.state = {"pairs": {}, "updated_at": utc_now()}

    def _pair_state(self, pair: str) -> Dict[str, Any]:
        key = str(pair or "").strip()
        pairs = self.state.setdefault("pairs", {})
        if not isinstance(pairs, dict):
            pairs = {}
            self.state["pairs"] = pairs
        row = pairs.setdefault(
            key,
            {
                "tier": self.COLD,
                "last_flow_ts": "",
                "consecutive_denies": 0,
                "updated_at": utc_now(),
            },
        )
        if not isinstance(row, dict):
            row = {"tier": self.COLD, "last_flow_ts": "", "consecutive_denies": 0, "updated_at": utc_now()}
            pairs[key] = row
        return row

    def classify(self, pair: str, flow_ready: bool, gate_state: Optional[str]) -> str:
        gate = str(gate_state or "").strip().upper()
        if not flow_ready or not gate:
            return self.COLD
        if gate in {"TRAP_BLOCK", "CHOP_BLOCK"}:
            return self.COLD
        if gate in {"LOCATION_BLOCK", "FLOW_DIRECTION_BLOCK"}:
            return self.WARM
        return self.HOT

    def process_event(self, pair: str, event_type: str, event_data: dict) -> None:
        key = str(pair or "").strip()
        if not key:
            return
        data = event_data if isinstance(event_data, dict) else {}
        event = str(event_type or "").strip().lower()
        row = self._pair_state(key)
        current = str(row.get("tier") or self.COLD).upper()
        now_text = utc_now()

        if event in {"flow", "flow.updated", "flow_ready"}:
            flow_ready = bool(data.get("flow_ready", data.get("data_ready", False)))
            gate_state = data.get("gate_state")
            row["last_flow_ts"] = now_text
            if flow_ready and current == self.COLD:
                row["tier"] = self.WARM
            else:
                row["tier"] = self.classify(key, flow_ready, gate_state)

        elif event in {"gate", "gate.evaluated", "gate_eval"}:
            allow = bool(data.get("allow", data.get("allowed", False)))
            deny_reason = str(data.get("deny_reason") or data.get("reason") or data.get("gate_state") or "").upper()
            if allow:
                row["consecutive_denies"] = 0
                if current in {self.COLD, self.WARM}:
                    row["tier"] = self.HOT
            else:
                row["consecutive_denies"] = int(row.get("consecutive_denies") or 0) + 1
                if current == self.WARM and deny_reason not in {"FLOW_TRAP_RISK", "CHOP_BLOCK"}:
                    row["tier"] = self.HOT
                if current == self.HOT and int(row.get("consecutive_denies") or 0) >= 3:
                    row["tier"] = self.WARM

        last_flow = self._parse_ts(row.get("last_flow_ts"))
        if str(row.get("tier") or "").upper() == self.WARM and last_flow is not None:
            age_sec = (datetime.now(timezone.utc) - last_flow).total_seconds()
            if age_sec >= 900:
                row["tier"] = self.COLD

        row["updated_at"] = now_text
        self.state["updated_at"] = now_text

    @staticmethod
    def _parse_ts(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def get_tier(self, pair: str) -> str:
        key = str(pair or "").strip()
        pairs = self.state.get("pairs", {})
        if not isinstance(pairs, dict):
            return self.COLD
        row = pairs.get(key, {})
        if not isinstance(row, dict):
            return self.COLD
        tier = str(row.get("tier") or self.COLD).upper()
        return tier if tier in self.TIERS else self.COLD

    def get_pairs_by_tier(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {self.HOT: [], self.WARM: [], self.COLD: []}
        pairs = self.state.get("pairs", {})
        if isinstance(pairs, dict):
            for pair, row in pairs.items():
                tier = self.COLD
                if isinstance(row, dict):
                    candidate = str(row.get("tier") or self.COLD).upper()
                    if candidate in self.TIERS:
                        tier = candidate
                out[tier].append(str(pair))
        for tier in out:
            out[tier] = sorted(set(out[tier]))
        return out

    def persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)


class RateBudgetManager:
    def __init__(self, rpm_limit: int = 100):
        try:
            configured = int(float(os.environ.get("BYBIT_COLLECTOR_RATE_LIMIT_RPM", str(rpm_limit))))
        except Exception:
            configured = rpm_limit
        self.rpm_limit = max(1, configured)
        self.request_times: List[float] = []
        self.backoff_until = 0.0
        self.hot_only_next_cycle = False

    def _trim(self) -> None:
        cutoff = time.time() - 60.0
        self.request_times = [ts for ts in self.request_times if ts >= cutoff]

    def can_request(self) -> bool:
        self._trim()
        return not self.is_in_backoff() and len(self.request_times) < self.rpm_limit

    def record_request(self) -> None:
        self._trim()
        self.request_times.append(time.time())

    def record_rate_limit_hit(self) -> None:
        self.backoff_until = time.time() + 30.0
        self.hot_only_next_cycle = True

    def is_in_backoff(self) -> bool:
        return time.time() < self.backoff_until

    def get_usage(self) -> Dict[str, Any]:
        self._trim()
        used = len(self.request_times)
        headroom_pct = max(0.0, ((self.rpm_limit - used) / float(self.rpm_limit)) * 100.0)
        return {
            "used": used,
            "limit": self.rpm_limit,
            "headroom_pct": round(headroom_pct, 2),
            "in_backoff": self.is_in_backoff(),
            "hot_only_next_cycle": self.hot_only_next_cycle,
        }


class CollectionScheduler:
    def __init__(self, hot_every_n: int = 1, warm_every_n: int = 2, cold_every_n: int = 4):
        self.hot_every_n = max(1, int(hot_every_n or 1))
        self.warm_every_n = max(1, int(warm_every_n or 2))
        self.cold_every_n = max(1, int(cold_every_n or 4))

    def get_collection_list(
        self,
        cycle_number: int,
        tiers: Dict[str, List[str]],
        rate_budget: RateBudgetManager,
    ) -> List[str]:
        cycle = max(1, int(cycle_number or 1))
        data = tiers if isinstance(tiers, dict) else {}
        include_warm = cycle % self.warm_every_n == 0
        include_cold = cycle % self.cold_every_n == 0
        if rate_budget.is_in_backoff() or getattr(rate_budget, "hot_only_next_cycle", False):
            include_warm = False
            include_cold = False
            rate_budget.hot_only_next_cycle = False

        selected: List[str] = []
        if cycle % self.hot_every_n == 0:
            selected.extend(str(pair) for pair in data.get("HOT", []) if pair)
        if include_warm:
            selected.extend(str(pair) for pair in data.get("WARM", []) if pair)
        if include_cold:
            selected.extend(str(pair) for pair in data.get("COLD", []) if pair)
        return sorted(dict.fromkeys(selected))

    def get_sleep_for_tier(self, tier: str) -> float:
        normalized = str(tier or "").upper()
        if normalized == "HOT":
            return 0.15
        if normalized == "WARM":
            return 0.25
        return 0.5


class BufferManager:
    def __init__(self, max_total: int = 150, buffer_size: int = 40):
        self.max_total = max(1, int(max_total or 150))
        self.buffer_size = max(0, int(buffer_size or 40))
        self.missing_cycles: Dict[str, int] = {}
        self.last_seen: Dict[str, str] = {}

    def update_buffer(
        self,
        watchlist: List[str],
        scanner_universe: List[str],
        tiers: Dict[str, List[str]],
        volumes: Dict[str, float],
    ) -> List[str]:
        watch = [str(pair) for pair in (watchlist or []) if pair]
        universe = [str(pair) for pair in (scanner_universe or []) if pair]
        tier_data = tiers if isinstance(tiers, dict) else {}
        volume_data = volumes if isinstance(volumes, dict) else {}

        universe_set = set(universe)
        for pair in set(watch) | set(self.missing_cycles):
            if pair in universe_set:
                self.record_scanner_appearance(pair)
            else:
                self.missing_cycles[pair] = int(self.missing_cycles.get(pair, 0)) + 1

        ranked_universe = sorted(
            universe_set,
            key=lambda pair: (-float(volume_data.get(pair, 0.0) or 0.0), pair),
        )
        buffer_pairs = ranked_universe[: self.buffer_size]

        retained_priority: List[str] = []
        for tier in ("HOT", "WARM", "COLD"):
            retained_priority.extend(str(pair) for pair in tier_data.get(tier, []) if pair)
        retained_priority.extend(watch)

        merged = list(dict.fromkeys(retained_priority + buffer_pairs + ranked_universe))
        return merged[: self.max_total]

    def record_scanner_appearance(self, pair: str) -> None:
        key = str(pair or "").strip()
        if not key:
            return
        self.missing_cycles[key] = 0
        self.last_seen[key] = utc_now()

    def get_eviction_candidates(self) -> List[str]:
        return sorted(pair for pair, count in self.missing_cycles.items() if int(count or 0) >= 3)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def pct(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / abs(old)) * 100.0


def request_json(path: str, params: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API_BASE}{path}?{query}"

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "revo-f2g-bybit-flow-collector/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                if int(data.get("retCode", 0)) != 0:
                    raise RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
                return data
        except Exception as e:
            if attempt >= retries:
                raise
            sleep_for = min(10.0, 1.5 * attempt)
            log(f"WARN request_retry attempt={attempt} path={path} error={e} sleep={sleep_for}")
            time.sleep(sleep_for)
    raise RuntimeError("unreachable")


def symbol_to_pair(symbol: str) -> str:
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}/USDT:USDT"
    return symbol


def pair_to_symbol(pair: str) -> str:
    return pair.replace(":USDT", "").replace("/", "")


def load_runtime_pair_symbols() -> List[str]:
    try:
        data = json.loads(PAIRLIST_FILE.read_text(encoding="utf-8"))
        pairs = data.get("pairs", [])
        symbols = [pair_to_symbol(str(p)) for p in pairs if str(p).endswith(":USDT")]
        return [s for s in symbols if s.endswith("USDT")]
    except Exception:
        return []


def get_tickers() -> List[Dict[str, Any]]:
    data = request_json("/v5/market/tickers", {"category": CATEGORY})
    rows = data.get("result", {}).get("list", [])
    return [r for r in rows if str(r.get("symbol", "")).endswith("USDT")]



# F4X_AS5J2C_SOURCE_SELECTION_V2_HELPERS
_PAIR_RE_F4X = re.compile(r"^[A-Z0-9]{2,50}/[A-Z0-9]{2,50}(:[A-Z0-9]{2,50})?$")

def _f4x_norm_pair_or_symbol(v):
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if _PAIR_RE_F4X.match(s):
        base, quote = s.split("/", 1)
        quote = quote.split(":", 1)[0]
        return base + quote
    if s.endswith("USDT") or s.endswith("USDC") or s.endswith("USD"):
        return s
    return None

def _f4x_as_float(v, default=None):
    try:
        if v in (None, "", "None"):
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default

def _f4x_walk_dict_records(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _f4x_walk_dict_records(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _f4x_walk_dict_records(v)

def _f4x_extract_symbol_from_record(d):
    if not isinstance(d, dict):
        return None
    for k in ("symbol", "pair", "market", "asset", "order_pair"):
        s = _f4x_norm_pair_or_symbol(d.get(k))
        if s:
            return s
    for sub in ("candidate", "raw", "data", "metric", "metrics", "flow", "trigger", "smc", "cvdoi"):
        x = d.get(sub)
        if isinstance(x, dict):
            s = _f4x_extract_symbol_from_record(x)
            if s:
                return s
    return None

def _f4x_extract_volume_usd(d):
    if not isinstance(d, dict):
        return None
    for k in ("quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd", "turnover24h", "quoteVolume24h", "volume24h", "volume_24h", "volume"):
        x = _f4x_as_float(d.get(k))
        if x is not None:
            return abs(x)
    for sub in ("candidate", "raw", "data", "metric", "metrics", "flow"):
        x = d.get(sub)
        if isinstance(x, dict):
            v = _f4x_extract_volume_usd(x)
            if v is not None:
                return v
    return None

def _f4x_read_json_file(path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return {}

def _f4x_add_symbol_weight(bucket, symbol, weight, source):
    if not symbol:
        return
    rec = bucket.get(symbol)
    if rec is None:
        bucket[symbol] = {"symbol": symbol, "weight": float(weight), "sources": [source]}
    else:
        rec["weight"] = max(float(rec.get("weight", 0)), float(weight))
        if source not in rec["sources"]:
            rec["sources"].append(source)

def _f4x_symbols_from_json(path, base_weight, source, min_volume_usd=0):
    out = {}
    obj = _f4x_read_json_file(path)
    for d in _f4x_walk_dict_records(obj):
        sym = _f4x_extract_symbol_from_record(d)
        if not sym:
            continue
        vol = _f4x_extract_volume_usd(d)
        if vol is not None and vol < min_volume_usd:
            continue
        weight = base_weight + min(100.0, (vol or 0) / 10000000.0)
        _f4x_add_symbol_weight(out, sym, weight, source)
    return out

def _f4x_merge_ranked(*buckets):
    merged = {}
    for bucket in buckets:
        for sym, rec in bucket.items():
            old = merged.get(sym)
            if old is None:
                merged[sym] = dict(rec)
            else:
                old["weight"] = max(float(old.get("weight", 0)), float(rec.get("weight", 0)))
                for s in rec.get("sources", []):
                    if s not in old["sources"]:
                        old["sources"].append(s)
    return sorted(merged.values(), key=lambda r: (float(r.get("weight", 0)), r.get("symbol", "")), reverse=True)


# F4X_AS5J2I0_MAJOR_PRIORITY_SOURCE_SELECTION_V2
# F4X_AS5J2I0 fixes AS5J2I v1:
# - preserves choose_symbols(tickers) signature
# - fixes USDT symbol conversion, e.g. BTC/USDT:USDT -> BTCUSDT, not BTCUSD
# - keeps ticker24h cold fallback
# Safety: collector source-selection only; no K/L/order/live/risk/gate.
F4X_AS5J2I0_MAJOR_BASES = [
    "BTC",
    "ETH",
    "XRP",
    "DOGE",
    "SUI",
    "ADA",
    "LINK",
    "TON",
    "SOL",
    "BNB",
    "ZEC",
    "HYPE",
    "NEAR",
    "INJ",
    "ONDO",
    "SAGA",
    "TRUMP",
    "1000PEPE",
    "PEPE",
    "TAO",
    "ENA",
    "TIA",
    "WLD",
    "DOT",
    "LTC",
    "ARB",
    "AVAX",
    "OP",
    "ATOM",
    "BCH",
    "ETC"
]

def _f4x_as5j2i0_norm_pair(v):
    import re
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if re.match(r"^[A-Z0-9]{1,60}/[A-Z0-9]{2,20}(:[A-Z0-9]{2,20})?$", s):
        if ":" not in s and s.endswith("/USDT"):
            return s + ":USDT"
        if ":" not in s and s.endswith("/USDC"):
            return s + ":USDC"
        return s
    if re.match(r"^[A-Z0-9]{1,60}(USDT|USDC|USD|PERP)$", s):
        if s.endswith("USDT"):
            return s[:-4] + "/USDT:USDT"
        if s.endswith("USDC"):
            return s[:-4] + "/USDC:USDC"
        if s.endswith("USD"):
            return s[:-3] + "/USD:USD"
    return None

def _f4x_as5j2i0_pair_to_symbol(pair):
    # F4X_AS5J2I0_FIX_USDT_SYMBOL_CONVERSION
    p = _f4x_as5j2i0_norm_pair(pair) or str(pair).upper()
    base = p.split("/", 1)[0]

    # Critical: check /USDT before /USD, because /USDT contains /USD prefix.
    if "/USDT" in p:
        quote = "USDT"
    elif "/USDC" in p:
        quote = "USDC"
    elif "/USD" in p:
        quote = "USD"
    else:
        quote = "USDT"

    return base + quote

def _f4x_as5j2i0_runtime_dir():
    import os
    from pathlib import Path
    rt = os.environ.get("REVO_RUNTIME_DIR") or globals().get("RUNTIME_DIR")
    if rt:
        return Path(rt)
    return Path("user_data/revo_alpha/runtime/bybit")

def _f4x_as5j2i0_load_json(path):
    import json
    from pathlib import Path
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(errors="replace"))
    except Exception:
        return None
    return None

def _f4x_as5j2i0_walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _f4x_as5j2i0_walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _f4x_as5j2i0_walk(v)

def _f4x_as5j2i0_extract_pair(d):
    if isinstance(d, str):
        return _f4x_as5j2i0_norm_pair(d)
    if not isinstance(d, dict):
        return None

    for k in ("pair", "symbol", "market", "asset", "order_pair"):
        p = _f4x_as5j2i0_norm_pair(d.get(k))
        if p:
            return p

    for v in d.values():
        p = _f4x_as5j2i0_norm_pair(v)
        if p:
            return p

    return None

def _f4x_as5j2i0_volume(d):
    keys = {
        "quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd",
        "turnover24h", "quoteVolume24h", "volume24h", "vol_usd",
        "volume", "volume_24h", "vol24h"
    }

    best = [0.0]

    def rec(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys:
                    try:
                        x = abs(float(v))
                        best[0] = max(best[0], x)
                    except Exception:
                        pass
                rec(v)
        elif isinstance(o, list):
            for v in o[:500]:
                rec(v)

    rec(d)
    return best[0]

def _f4x_as5j2i0_collect_pairs(obj, source_name, out):
    for d in _f4x_as5j2i0_walk(obj):
        p = _f4x_as5j2i0_extract_pair(d)
        if not p:
            continue

        slot = out.setdefault(p, {"sources": [], "volume": 0.0, "missing": []})
        if source_name not in slot["sources"]:
            slot["sources"].append(source_name)

        v = _f4x_as5j2i0_volume(d)
        if v:
            slot["volume"] = max(float(slot.get("volume") or 0.0), float(v))

        if isinstance(d, dict):
            for key in ("missing", "missing_reasons", "missing_keys", "reasons"):
                mv = d.get(key)
                if isinstance(mv, list):
                    for m in mv:
                        if str(m) not in slot["missing"]:
                            slot["missing"].append(str(m))

def _f4x_as5j2i0_add_tickers(tickers, out):
    # F4X_AS5J2I0_TICKER_COLD_FALLBACK
    if not tickers:
        return

    for t in tickers:
        if not isinstance(t, dict):
            continue
        sym = str(t.get("symbol") or "").upper()
        p = _f4x_as5j2i0_norm_pair(sym)
        if not p:
            continue

        slot = out.setdefault(p, {"sources": [], "volume": 0.0, "missing": []})
        if "ticker24h" not in slot["sources"]:
            slot["sources"].append("ticker24h")

        vol = 0.0
        for k in ("turnover24h", "volume24h", "quoteVolume24h", "quoteVolume", "volume"):
            try:
                vol = max(vol, abs(float(t.get(k) or 0.0)))
            except Exception:
                pass
        if vol:
            slot["volume"] = max(float(slot.get("volume") or 0.0), vol)

def choose_symbols(tickers: List[Dict[str, Any]]) -> List[str]:
    # F4X_AS5J2I0_PRESERVE_CHOOSE_SYMBOLS_SIGNATURE
    # F4X_AS5J2I0_CVD_MISSING_PRIORITY
    import os

    rt = _f4x_as5j2i0_runtime_dir()
    max_symbols = int(os.environ.get("BYBIT_COLLECTOR_MAX_SYMBOLS", globals().get("MAX_SYMBOLS_PER_CYCLE", globals().get("TOP_N", 120))))
    hot_limit = int(os.environ.get("BYBIT_COLLECTOR_HOT_LIMIT", 40))
    major_limit = int(os.environ.get("BYBIT_COLLECTOR_MAJOR_LIMIT", 40))
    cvd_missing_limit = int(os.environ.get("BYBIT_COLLECTOR_CVD_MISSING_LIMIT", 80))
    min_volume = float(os.environ.get("BYBIT_COLLECTOR_MIN_VOLUME_USD", 4000000))

    src = {}
    files = [
        ("pair_universe", globals().get("PAIRLIST_FILE") or (rt / "pair_universe_remote.json")),
        ("flow_context", rt / "revo_flow_context_collector.json"),
        ("as5j1_full", rt / "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_FULL.json"),
        ("f3a_b", rt / "revo_f3a_b_flow_cache_health_classifier_state.json"),
        ("f3b", rt / "revo_f3b_regime_aware_oi_interpreter_state.json"),
        ("feeder_raw", rt / "F4X_LEGACY_FEEDER_RAW_UNIVERSE_REPORT_ONLY.json"),
        ("feeder_lanes", rt / "F4X_LEGACY_FEEDER_HOT_WARM_COLD_REPORT_ONLY.json"),
        ("full", rt / "F4X_FULL_CONFLUENCE_FINAL_FULL.json"),
        ("paper", rt / "F4X_PAPER_DECISION_SIGNALS.json"),
    ]

    for name, path in files:
        obj = _f4x_as5j2i0_load_json(path)
        if obj is not None:
            _f4x_as5j2i0_collect_pairs(obj, name, src)

    _f4x_as5j2i0_add_tickers(tickers, src)

    def vol(p):
        return float(src.get(p, {}).get("volume") or 0.0)

    selected = []
    selected_set = set()

    def add(p):
        p = _f4x_as5j2i0_norm_pair(p)
        if not p or p in selected_set:
            return
        selected_set.add(p)
        selected.append(p)

    current_flow = [p for p, r in src.items() if "flow_context" in r.get("sources", [])]
    current_flow.sort(key=lambda p: (vol(p), p), reverse=True)

    # Major priority first.
    major_pairs = [
        p for p in src
        if p.split("/", 1)[0] in F4X_AS5J2I0_MAJOR_BASES
        and (vol(p) >= min_volume or p in current_flow)
    ]
    major_pairs.sort(key=lambda p: (vol(p), p), reverse=True)
    for p in major_pairs[:major_limit]:
        add(p)

    # Keep hot flow-context seeds.
    for p in current_flow[:hot_limit]:
        add(p)

    # Prioritize high-volume CVD-missing rows from AS5J1.
    cvd_missing = [
        p for p, r in src.items()
        if "CVD_MISSING" in r.get("missing", []) and vol(p) >= min_volume
    ]
    cvd_missing.sort(key=lambda p: ((p.split("/", 1)[0] in F4X_AS5J2I0_MAJOR_BASES), vol(p), p), reverse=True)
    for p in cvd_missing[:cvd_missing_limit]:
        add(p)

    # High-volume fill from broad universe and ticker fallback.
    high_volume = [p for p in src if vol(p) >= min_volume]
    high_volume.sort(key=lambda p: (vol(p), p), reverse=True)
    for p in high_volume:
        add(p)

    # Final fallback keeps prior flow seeds.
    for p in current_flow:
        add(p)

    out = []
    seen_symbols = set()
    for p in selected:
        sym = _f4x_as5j2i0_pair_to_symbol(p)
        if not sym.endswith("USDT"):
            continue
        if sym not in seen_symbols:
            seen_symbols.add(sym)
            out.append(sym)
        if len(out) >= max_symbols:
            break

    return out
def get_kline_delta(symbol: str, interval: str, limit: int = 4) -> Tuple[float, float]:
    try:
        time.sleep(HTTP_SLEEP_SEC)
        data = request_json("/v5/market/kline", {
            "category": CATEGORY,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        rows = data.get("result", {}).get("list", [])
        if not rows:
            return 0.0, 0.0

        # Bybit returns newest first. Each row: start, open, high, low, close, volume, turnover.
        newest = rows[0]
        oldest = rows[-1]
        close_now = fnum(newest[4])
        open_old = fnum(oldest[1])
        vol_values = [fnum(r[5]) for r in rows if len(r) > 5]
        return pct(close_now, open_old), sum(vol_values)
    except Exception as e:
        log(f"WARN kline_error symbol={symbol} interval={interval} error={e}")
        return 0.0, 0.0


def get_open_interest_delta(symbol: str, interval_time: str, limit: int = 5) -> float:
    try:
        time.sleep(HTTP_SLEEP_SEC)
        data = request_json("/v5/market/open-interest", {
            "category": CATEGORY,
            "symbol": symbol,
            "intervalTime": interval_time,
            "limit": limit,
        })
        rows = data.get("result", {}).get("list", [])
        if len(rows) < 2:
            return 0.0
        # Newest first.
        newest = fnum(rows[0].get("openInterest"))
        oldest = fnum(rows[-1].get("openInterest"))
        return pct(newest, oldest)
    except Exception as e:
        log(f"WARN oi_error symbol={symbol} interval={interval_time} error={e}")
        return 0.0


def get_cvd_proxy(symbol: str) -> Tuple[float, float]:
    try:
        time.sleep(HTTP_SLEEP_SEC)
        data = request_json("/v5/market/recent-trade", {
            "category": CATEGORY,
            "symbol": symbol,
            "limit": TRADE_LIMIT,
        })
        rows = data.get("result", {}).get("list", [])
        cvd = 0.0
        total = 0.0
        for r in rows:
            size = fnum(r.get("size"))
            price = fnum(r.get("price"))
            notional = size * price
            side = str(r.get("side", "")).lower()
            if side == "buy":
                cvd += notional
            elif side == "sell":
                cvd -= notional
            total += abs(notional)
        z = 0.0 if total == 0 else max(-5.0, min(5.0, (cvd / total) * 5.0))
        return cvd, z
    except Exception as e:
        log(f"WARN trade_error symbol={symbol} error={e}")
        return 0.0, 0.0


def build_row(symbol: str, ticker: Dict[str, Any]) -> Dict[str, Any]:
    last_price = fnum(ticker.get("lastPrice"))
    p1h = pct(last_price, fnum(ticker.get("prevPrice1h"), last_price))
    p24 = fnum(ticker.get("price24hPcnt")) * 100.0
    p15, vol_recent = get_kline_delta(symbol, "15", 3)
    oi15 = get_open_interest_delta(symbol, "15min", 3)
    oi1h = get_open_interest_delta(symbol, "1h", 3)
    cvd, cvd_z = get_cvd_proxy(symbol)

    quote_vol = fnum(ticker.get("turnover24h", ticker.get("volume24h", 0)))
    funding = fnum(ticker.get("fundingRate"))

    row = {
        "pair": symbol_to_pair(symbol),
        "symbol": symbol,
        "ts": utc_now(),
        "source": "BYBIT_TICKER",
        "collector_source": "BYBIT_V5_PUBLIC_REST",
        "last_price": last_price,
        "price_delta_pct_15m": p15,
        "price_delta_pct_1h": p1h,
        "oi_delta_pct_15m": oi15,
        "oi_delta_pct_1h": oi1h,
        "cvd_delta_15m": cvd,
        "cvd_zscore_15m": cvd_z,
        "cvd_source": "BYBIT_RECENT_PUBLIC_TRADES",
        "funding_rate": funding,
        "funding_zscore": max(-5.0, min(5.0, funding * 10000.0)),
        "volume_zscore_15m": 0.0,
        "quote_volume_24h": quote_vol,
        "recent_volume_proxy": vol_recent,
        "price_change_24h_pct": p24,
        "data_ready": True,
        "data_quality": "OK",
        "missing_fields": "",
        # aliases
        "price_delta_15m_pct": p15,
        "price_change_15m_pct": p15,
        "p15": p15,
        "price_delta_1h_pct": p1h,
        "price_change_1h_pct": p1h,
        "p1h": p1h,
        "oi_delta_15m_pct": oi15,
        "open_interest_delta_15m_pct": oi15,
        "oi15": oi15,
        "oi_delta_1h_pct": oi1h,
        "open_interest_delta_1h_pct": oi1h,
        "oi1h": oi1h,
        "cvd_zscore": cvd_z,
        "cvd_z": cvd_z,
        "cvd_z_15m": cvd_z,
        "cvd_delta": cvd,
        "volume_zscore": 0.0,
        "volume_z": 0.0,
        "volume_z_15m": 0.0,
    }
    return row


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_outputs(rows: List[Dict[str, Any]], cycle_id: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    data = {r["pair"]: r for r in rows}
    atomic_write_text(OUT_JSON, json.dumps(data, indent=2, ensure_ascii=False, default=str))

    fieldnames = [
        "pair", "symbol", "ts", "source", "last_price",
        "price_delta_pct_15m", "price_delta_pct_1h",
        "oi_delta_pct_15m", "oi_delta_pct_1h",
        "cvd_delta_15m", "cvd_zscore_15m", "funding_rate",
        "volume_zscore_15m", "quote_volume_24h", "price_change_24h_pct",
        "data_quality",
    ]
    tmp_csv = OUT_CSV.with_suffix(".csv.tmp")
    with tmp_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    tmp_csv.replace(OUT_CSV)

    hb = {
        "event": "BYBIT_FLOW_COLLECTOR_HEARTBEAT",
        "generated_at": utc_now(),
        "cycle_id": cycle_id,
        "runtime_dir": str(RUNTIME_DIR),
        "rows": len(rows),
        "source": "BYBIT_V5_PUBLIC_REST",
        "category": CATEGORY,
        "top_n": TOP_N,
        "interval_sec": INTERVAL_SEC,
        "output_json": str(OUT_JSON),
        "output_csv": str(OUT_CSV),
        "note": "collector-only; does not write revo_flow_context.json",
    }

    atomic_write_text(HEARTBEAT_JSON, json.dumps(hb, indent=2, ensure_ascii=False))
    with HEARTBEAT_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(hb, ensure_ascii=False) + "\n")

    compact = [
        "BYBIT_FLOW_COLLECTOR_COMPACT",
        f"generated_at={hb['generated_at']}",
        f"cycle_id={cycle_id}",
        f"runtime_dir={RUNTIME_DIR}",
        f"rows={len(rows)}",
        f"output_json={OUT_JSON}",
        f"output_csv={OUT_CSV}",
        "collector_only=1",
        "canonical_write=0",
        "sample_pairs=" + ",".join([r["pair"] for r in rows[:10]]),
    ]
    atomic_write_text(HEARTBEAT, "\n".join(compact) + "\n")


def run_once() -> int:
    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log(f"START_BYBIT_FLOW_COLLECTOR cycle={cycle_id} runtime={RUNTIME_DIR}")

    tickers = get_tickers()
    tick_map = {str(r.get("symbol")): r for r in tickers}
    symbols = choose_symbols(tickers)

    rows: List[Dict[str, Any]] = []
    errors = 0

    for i, symbol in enumerate(symbols, 1):
        try:
            ticker = tick_map.get(symbol)
            if not ticker:
                continue
            row = build_row(symbol, ticker)
            rows.append(row)
            log(f"COLLECTED {i}/{len(symbols)} {symbol}")
        except Exception as e:
            errors += 1
            log(f"WARN collect_symbol_error symbol={symbol} error={e}")
        time.sleep(HTTP_SLEEP_SEC)

    write_outputs(rows, cycle_id)
    log(f"BYBIT_FLOW_COLLECTOR_PASS cycle={cycle_id} rows={len(rows)} errors={errors}")
    return 0 if rows else 1


def main() -> int:
    if "--once" in sys.argv:
        return run_once()

    while True:
        try:
            run_once()
        except Exception as e:
            log(f"ERROR cycle_failed error={e}")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
