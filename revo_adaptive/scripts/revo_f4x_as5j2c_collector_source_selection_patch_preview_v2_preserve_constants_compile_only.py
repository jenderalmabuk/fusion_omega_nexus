#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2C_COLLECTOR_SOURCE_SELECTION_PATCH_PREVIEW_V2_PRESERVE_CONSTANTS_COMPILE_ONLY"
MODE = "COLLECTOR_SOURCE_SELECTION_PATCH_PREVIEW_V2_PRESERVE_CONSTANTS_AND_COMPILE_ONLY"

TARGET_REL = "scripts/bybit_flow_live_collector.py"

REQUIRED_CONSTANTS = [
    "TRADE_LIMIT",
    "HTTP_SLEEP_SEC",
    "HTTP_TIMEOUT_SEC",
    "OUT_JSON",
    "OUT_CSV",
    "HEARTBEAT",
    "HEARTBEAT_JSON",
    "HEARTBEAT_JSONL",
    "LOG_FILE",
]

RUNTIME_FILES = {
    "as5j2b": "F4X_AS5J2B_COLLECTOR_SOURCE_SELECTION_PATCH_PREVIEW_AUDIT_ACTIVE.json",
    "as5j2a": "F4X_AS5J2A_CVD_OVERLAY_SOURCE_AND_SCHEMA_BRIDGE_PREVIEW_AUDIT_ACTIVE.json",
    "feeder_raw": "F4X_LEGACY_FEEDER_RAW_UNIVERSE_REPORT_ONLY.json",
    "feeder_lanes": "F4X_LEGACY_FEEDER_HOT_WARM_COLD_REPORT_ONLY.json",
    "f3a_b": "revo_f3a_b_flow_cache_health_classifier_state.json",
    "f3b": "revo_f3b_regime_aware_oi_interpreter_state.json",
    "full": "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
    "as5": "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json",
    "pair_universe": "pair_universe_remote.json",
    "flow_context": "revo_flow_context_collector.json",
    "k": "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json",
    "l": "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json",
}

PAIR_RE = re.compile(r"^[A-Z0-9]{2,50}/[A-Z0-9]{2,50}(:[A-Z0-9]{2,50})?$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,50}(USDT|USDC|USD|PERP)$")

VOLUME_KEYS = [
    "quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd",
    "turnover24h", "quoteVolume24h", "volume24h", "volume", "volume_24h",
]

INSERT_CONSTANTS = '''
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
'''

HELPERS = r'''
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
'''

CHOOSE_SYMBOLS = r'''def choose_symbols(tickers: List[Dict[str, Any]]) -> List[str]:
    """
    F4X_AS5J2C source-selection v2:
    - Preserves original collector outputs, HTTP knobs, and trade-limit constants.
    - pair_universe remains HOT seed, but no longer blocks feeder/F3 high-volume universe.
    - feeder_raw / feeder_lanes / f3a_b / f3b provide WARM candidates.
    - ticker24h fallback provides COLD rotation when API tickers are available.
    """
    hot = {}
    warm = {}
    cold = {}

    if USE_PAIR_UNIVERSE:
        hot.update(_f4x_symbols_from_json(PAIRLIST_FILE, 10000, "pair_universe", 0))

    hot.update(_f4x_symbols_from_json(FULL_FILE, 9500, "full", 0))
    hot.update(_f4x_symbols_from_json(AS5_FILE, 9000, "as5", 0))

    if USE_FEEDER_RAW:
        warm.update(_f4x_symbols_from_json(FEEDER_RAW_FILE, 6500, "feeder_raw", MIN_VOLUME_USD))
        warm.update(_f4x_symbols_from_json(FEEDER_LANES_FILE, 6400, "feeder_lanes", MIN_VOLUME_USD))

    if USE_F3_STATES:
        warm.update(_f4x_symbols_from_json(F3A_B_FILE, 5500, "f3a_b", MIN_VOLUME_USD))
        warm.update(_f4x_symbols_from_json(F3B_FILE, 5400, "f3b", MIN_VOLUME_USD))

    for t in tickers:
        sym = str(t.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        vol = _f4x_as_float(t.get("turnover24h"), 0.0) or _f4x_as_float(t.get("volume24h"), 0.0) or 0.0
        if vol < MIN_VOLUME_USD:
            continue
        _f4x_add_symbol_weight(cold, sym, 1000 + min(100.0, vol / 10000000.0), "ticker24h")

    ranked_hot = _f4x_merge_ranked(hot)[:HOT_LIMIT]
    ranked_warm = _f4x_merge_ranked(warm)[:WARM_LIMIT]
    ranked_cold = _f4x_merge_ranked(cold)[:COLD_LIMIT]

    chosen = []
    seen = set()
    for row in ranked_hot + ranked_warm + ranked_cold:
        sym = row.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        chosen.append(sym)
        if len(chosen) >= MAX_SYMBOLS_PER_CYCLE:
            break

    return chosen[:MAX_SYMBOLS_PER_CYCLE]
'''


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def inspect_k(k: Any) -> dict[str, Any]:
    if not isinstance(k, dict) or not k:
        return {"clean": False, "reasons": ["K_MISSING_OR_INVALID"]}
    reasons = []
    order_intents = k.get("order_intents") if isinstance(k.get("order_intents"), list) else []
    checks = [
        ("has_order_intent", False),
        ("paper_order_allowed", False),
        ("allow_paper_entry", False),
        ("would_order", False),
        ("dry_run_only", True),
        ("live_allowed", False),
        ("risk_up_allowed", False),
        ("gate_loosen_allowed", False),
    ]
    for key, expected in checks:
        if bool(k.get(key)) is not expected if isinstance(k.get(key), bool) else k.get(key) is not expected:
            if k.get(key) is not None:
                reasons.append(f"{key.upper()}_NOT_{expected}:{k.get(key)}")
    if order_intents:
        reasons.append(f"ORDER_INTENTS_NOT_EMPTY:{len(order_intents)}")
    try:
        if int(k.get("intent_count") or 0) != 0:
            reasons.append(f"INTENT_COUNT_NOT_ZERO:{k.get('intent_count')}")
    except Exception:
        pass
    return {"clean": not reasons, "reasons": reasons or ["K_CLEAN"], "mode": k.get("mode"), "intent_count": k.get("intent_count"), "has_order_intent": k.get("has_order_intent"), "order_intents_len": len(order_intents)}


def inspect_l(l: Any) -> dict[str, Any]:
    if not isinstance(l, dict) or not l:
        return {"clean": True, "reasons": ["L_MISSING_OR_EMPTY_TREATED_CLEAN"]}
    decision = str(l.get("decision") or "NO_VALID_ORDER_INTENT")
    orders = l.get("orders") if isinstance(l.get("orders"), list) else []
    errors = l.get("errors") if isinstance(l.get("errors"), list) else []
    reasons = []
    if decision not in {"NO_VALID_ORDER_INTENT", "HOLD", ""}:
        reasons.append(f"L_DECISION_NOT_CLEAN:{decision}")
    if orders:
        reasons.append(f"L_ACTIVE_ORDERS_PRESENT:{len(orders)}")
    if errors:
        reasons.append(f"L_ACTIVE_ERRORS_PRESENT:{len(errors)}")
    return {"clean": not reasons, "reasons": reasons or ["L_CLEAN"], "decision": decision, "orders_count": len(orders), "errors_count": len(errors)}


def ensure_import(src: str, name: str) -> str:
    if re.search(rf"(^|\n)import {re.escape(name)}(\n|$)", src):
        return src
    return re.sub(r"(^import .*$)", rf"\1\nimport {name}", src, count=1, flags=re.M)


def preserve_replace(src: str) -> tuple[str, list[str]]:
    warnings = []
    out = ensure_import(src, "re")

    # Only change TOP_N default, preserve all other original constants.
    out, n_top = re.subn(
        r'TOP_N\s*=\s*int\(float\(os\.environ\.get\("BYBIT_COLLECTOR_TOP_N",\s*"[^"]+"\)\)\)',
        'TOP_N = int(float(os.environ.get("BYBIT_COLLECTOR_TOP_N", "150")))',
        out,
        count=1,
    )
    if n_top != 1:
        warnings.append("TOP_N_LINE_REPLACE_COUNT_NOT_1")

    if "F4X_AS5J2C_SOURCE_SELECTION_V2_ADDITIVE_CONSTANTS" not in out:
        m = re.search(r'PAIRLIST_FILE\s*=\s*RUNTIME_DIR\s*/\s*"pair_universe_remote\.json"\s*\n', out)
        if m:
            out = out[:m.end()] + INSERT_CONSTANTS + "\n" + out[m.end():]
        else:
            warnings.append("PAIRLIST_FILE_LINE_NOT_FOUND_CONSTANTS_NOT_INSERTED")

    if "F4X_AS5J2C_SOURCE_SELECTION_V2_HELPERS" not in out:
        idx = out.find("\ndef choose_symbols")
        if idx >= 0:
            out = out[:idx] + "\n" + HELPERS + "\n" + out[idx:]
        else:
            warnings.append("choose_symbols_NOT_FOUND_HELPERS_NOT_INSERTED")

    sig = re.search(r'\ndef choose_symbols\(tickers: List\[Dict\[str, Any\]\]\) -> List\[str\]:\n', out)
    if not sig:
        warnings.append("choose_symbols_SIGNATURE_NOT_FOUND")
        return out, warnings

    start = sig.start() + 1
    next_def = re.search(r'\n(?:def|async def)\s+\w+\(', out[sig.end():])
    end = sig.end() + next_def.start() if next_def else len(out)
    out = out[:start] + CHOOSE_SYMBOLS + "\n\n" + out[end:]
    return out, warnings


def constants_present(src: str) -> dict[str, bool]:
    return {c: bool(re.search(rf"(^|\n){re.escape(c)}\s*=", src)) for c in REQUIRED_CONSTANTS}


def norm_symbol(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if PAIR_RE.match(s):
        base, quote = s.split("/", 1)
        return base + quote.split(":", 1)[0]
    if SYMBOL_RE.match(s):
        return s
    return None


def as_num(v: Any) -> float | None:
    try:
        if v in (None, "", "None"):
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def extract_symbol(d: dict[str, Any]) -> str | None:
    for k in ("symbol", "pair", "market", "asset", "order_pair"):
        s = norm_symbol(d.get(k))
        if s:
            return s
    for sub in ("candidate", "raw", "data", "metric", "metrics", "flow"):
        x = d.get(sub)
        if isinstance(x, dict):
            s = extract_symbol(x)
            if s:
                return s
    return None


def extract_volume(d: dict[str, Any]) -> float | None:
    for k in VOLUME_KEYS:
        x = as_num(d.get(k))
        if x is not None:
            return abs(x)
    for sub in ("candidate", "raw", "data", "metric", "metrics", "flow"):
        x = d.get(sub)
        if isinstance(x, dict):
            v = extract_volume(x)
            if v is not None:
                return v
    return None


def bucket(obj: Any, source: str, base_weight: float, min_volume: float = 0) -> dict[str, dict[str, Any]]:
    out = {}
    for d in walk(obj):
        if not isinstance(d, dict):
            continue
        sym = extract_symbol(d)
        if not sym:
            continue
        vol = extract_volume(d)
        if vol is not None and vol < min_volume:
            continue
        weight = base_weight + min(100, (vol or 0) / 10000000)
        old = out.get(sym)
        if old is None:
            out[sym] = {"symbol": sym, "weight": weight, "volume_usd": vol, "sources": [source]}
        else:
            old["weight"] = max(old["weight"], weight)
            old["volume_usd"] = max(old.get("volume_usd") or 0, vol or 0)
            if source not in old["sources"]:
                old["sources"].append(source)
    return out


def merge(*buckets):
    out = {}
    for b in buckets:
        for sym, rec in b.items():
            old = out.get(sym)
            if old is None:
                out[sym] = dict(rec)
            else:
                old["weight"] = max(old["weight"], rec["weight"])
                old["volume_usd"] = max(old.get("volume_usd") or 0, rec.get("volume_usd") or 0)
                for s in rec.get("sources", []):
                    if s not in old["sources"]:
                        old["sources"].append(s)
    return sorted(out.values(), key=lambda r: (r["weight"], r.get("volume_usd") or 0, r["symbol"]), reverse=True)


def simulate(data: dict[str, Any], min_volume: float, hot_limit: int, warm_limit: int, max_symbols: int) -> dict[str, Any]:
    hot = merge(
        bucket(data.get("pair_universe") or {}, "pair_universe", 10000, 0),
        bucket(data.get("full") or {}, "full", 9500, 0),
        bucket(data.get("as5") or {}, "as5", 9000, 0),
    )[:hot_limit]
    warm = merge(
        bucket(data.get("feeder_raw") or {}, "feeder_raw", 6500, min_volume),
        bucket(data.get("feeder_lanes") or {}, "feeder_lanes", 6400, min_volume),
        bucket(data.get("f3a_b") or {}, "f3a_b", 5500, min_volume),
        bucket(data.get("f3b") or {}, "f3b", 5400, min_volume),
    )[:warm_limit]

    chosen = []
    seen = set()
    for r in hot + warm:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        chosen.append(r)
        if len(chosen) >= max_symbols:
            break

    current_flow = set(bucket(data.get("flow_context") or {}, "flow_context", 0, 0).keys())
    missing = [r for r in chosen if r["symbol"] not in current_flow]
    return {
        "hot_count": len(hot),
        "warm_count": len(warm),
        "chosen_count": len(chosen),
        "current_flow_symbol_count": len(current_flow),
        "chosen_missing_from_current_flow_count": len(missing),
        "source_counts": Counter(s for r in chosen for s in r.get("sources", [])).most_common(),
        "chosen": chosen,
        "chosen_missing_from_current_flow": missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--min-volume-usd", type=float, default=4000000.0)
    ap.add_argument("--hot-limit", type=int, default=40)
    ap.add_argument("--warm-limit", type=int, default=90)
    ap.add_argument("--max-symbols", type=int, default=150)
    ap.add_argument("--top-n", type=int, default=80)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    target = repo / TARGET_REL

    data = {name: read_json(runtime / fname, {}) for name, fname in RUNTIME_FILES.items()}
    src = read_text(target)

    failures = []
    warnings = []

    if not src:
        failures.append("TARGET_SOURCE_MISSING_OR_EMPTY")

    k_state = inspect_k(data.get("k"))
    l_state = inspect_l(data.get("l"))

    if not k_state.get("clean"):
        warnings.append("CURRENT_K_NOT_CLEAN")
    if not l_state.get("clean"):
        warnings.append("CURRENT_L_NOT_CLEAN")

    proposed, patch_warnings = preserve_replace(src)
    warnings.extend(patch_warnings)

    before_constants = constants_present(src)
    after_constants = constants_present(proposed)
    missing_constants_after = [k for k, v in after_constants.items() if not v]
    if missing_constants_after:
        failures.append("REQUIRED_CONSTANTS_MISSING_AFTER_PREVIEW:" + ",".join(missing_constants_after))

    preview_path = runtime / "F4X_AS5J2C_bybit_flow_live_collector_PREVIEW_V2_PRESERVE_CONSTANTS.py"
    diff_path = runtime / "F4X_AS5J2C_bybit_flow_live_collector_SOURCE_SELECTION_PREVIEW_V2.diff"
    sim_path = runtime / "F4X_AS5J2C_COLLECTOR_SOURCE_SELECTION_SIMULATION_V2_REPORT_ONLY.json"

    preview_path.write_text(proposed, encoding="utf-8")
    diff_lines = list(difflib.unified_diff(
        src.splitlines(),
        proposed.splitlines(),
        fromfile=TARGET_REL,
        tofile=TARGET_REL + ".AS5J2C_PREVIEW_V2",
        lineterm="",
    ))
    diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")

    compile_cmd = ["python3", "-m", "py_compile", str(preview_path)]
    compile_proc = subprocess.run(compile_cmd, text=True, capture_output=True)
    compile_ok = compile_proc.returncode == 0
    if not compile_ok:
        failures.append("PREVIEW_SOURCE_COMPILE_FAILED")

    sim = simulate(data, args.min_volume_usd, args.hot_limit, args.warm_limit, args.max_symbols)
    write_json(sim_path, sim)

    if failures:
        final_decision = "F4X_AS5J2C_PREVIEW_V2_HOLD_REVIEW_REQUIRED"
        next_action = "Do not execute. Review failures/warnings and preview source."
    elif sim.get("chosen_missing_from_current_flow_count", 0) > 50:
        final_decision = "F4X_AS5J2C_PREVIEW_V2_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "Preview v2 preserves constants and compiles. Next may execute backup+patch+compile only, no restart/order."
    else:
        final_decision = "F4X_AS5J2C_PREVIEW_V2_COMPILES_BUT_LIMITED_IMPACT_REVIEW"
        next_action = "Review impact before any execute."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": MODE,
        "target": str(target),
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "k_clean_hold_reset_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "source_overwrite_allowed": False,
        "api_call_allowed": False,
        "restart_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "k_state": k_state,
        "l_state": l_state,
        "compile": {
            "ok": compile_ok,
            "returncode": compile_proc.returncode,
            "stdout": compile_proc.stdout,
            "stderr": compile_proc.stderr,
            "cmd": compile_cmd,
        },
        "constants": {
            "before": before_constants,
            "after": after_constants,
            "missing_after": missing_constants_after,
        },
        "patch_preview": {
            "diff_line_count": len(diff_lines),
            "preview_source": str(preview_path),
            "diff": str(diff_path),
        },
        "simulation_summary": {
            "hot_count": sim.get("hot_count"),
            "warm_count": sim.get("warm_count"),
            "chosen_count": sim.get("chosen_count"),
            "current_flow_symbol_count": sim.get("current_flow_symbol_count"),
            "chosen_missing_from_current_flow_count": sim.get("chosen_missing_from_current_flow_count"),
            "source_counts": sim.get("source_counts"),
        },
        "report_only_output_files": {
            "preview_source": str(preview_path),
            "diff": str(diff_path),
            "simulation": str(sim_path),
        },
        "decision_policy": [
            "AS5J2C is preview/compile-only.",
            "AS5J2C does not overwrite source.",
            "AS5J2C does not call APIs.",
            "AS5J2C does not restart collector.",
            "AS5J2C does not write K or execute L.",
            "Any real patch requires separate explicit execute approval.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5J2C_COLLECTOR_SOURCE_SELECTION_PATCH_PREVIEW_V2_PRESERVE_CONSTANTS_COMPILE_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        f"target={target}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "k_clean_hold_reset=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "source_overwrite=HOLD",
        "api_call=HOLD",
        "restart=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        f"next_action={next_action}",
        "FAILURES",
        *(failures if failures else ["NONE"]),
        "WARNINGS",
        *(warnings if warnings else ["NONE"]),
        "K_L_STATE",
        f"k_state={k_state}",
        f"l_state={l_state}",
        "CONSTANT_PRESERVATION",
        f"required_constants={REQUIRED_CONSTANTS}",
        f"before={before_constants}",
        f"after={after_constants}",
        f"missing_after={missing_constants_after}",
        "COMPILE",
        f"compile_ok={compile_ok}|returncode={compile_proc.returncode}",
        f"compile_stdout={compile_proc.stdout.strip()}",
        f"compile_stderr={compile_proc.stderr.strip()}",
        "PATCH_PREVIEW",
        f"diff_line_count={len(diff_lines)}",
        f"preview_source={preview_path}",
        f"diff={diff_path}",
        "SIMULATION_SUMMARY",
        str(result["simulation_summary"]),
        "CHOSEN_MISSING_FROM_CURRENT_FLOW_SAMPLE",
    ]

    for r in sim.get("chosen_missing_from_current_flow", [])[:args.top_n]:
        lines.append(
            f"{r.get('symbol')}|weight={r.get('weight')}|vol={r.get('volume_usd')}|sources={r.get('sources')}"
        )

    lines.extend([
        "DIFF_HEAD",
        *diff_lines[:180],
        "REPORT_ONLY_OUTPUT_FILES",
        f"preview_source={preview_path}",
        f"diff={diff_path}",
        f"simulation={sim_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
    ])

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
