#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2I_REMAINING_CVD_COVERAGE_AND_MAJOR_PRIORITY_PATCH_PREVIEW_ONLY"
MODE = "REMAINING_CVD_COVERAGE_AND_MAJOR_PRIORITY_PATCH_PREVIEW_ONLY"

COLLECTOR_REL = "scripts/bybit_flow_live_collector.py"

AS5J1_ACTIVE = "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_ACTIVE.json"
AS5J1_FULL = "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_FULL.json"
AS5J1_COMPACT = "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_COMPACT.txt"

FLOW_CONTEXT = "revo_flow_context_collector.json"
F3A_B = "revo_f3a_b_flow_cache_health_classifier_state.json"
F3B = "revo_f3b_regime_aware_oi_interpreter_state.json"
FEEDER_RAW = "F4X_LEGACY_FEEDER_RAW_UNIVERSE_REPORT_ONLY.json"
FEEDER_LANES = "F4X_LEGACY_FEEDER_HOT_WARM_COLD_REPORT_ONLY.json"
PAIR_UNIVERSE = "pair_universe_remote.json"
FULL = "F4X_FULL_CONFLUENCE_FINAL_FULL.json"
PAPER = "F4X_PAPER_DECISION_SIGNALS.json"

K_FILE = "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
L_FILE = "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"

PAIR_RE = re.compile(r"^[A-Z0-9]{1,60}/[A-Z0-9]{2,20}(:[A-Z0-9]{2,20})?$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,60}(USDT|USDC|USD|PERP)$")

VOLUME_KEYS = [
    "quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd",
    "turnover24h", "quoteVolume24h", "volume24h", "vol_usd",
    "volume", "volume_24h", "vol24h"
]

MAJOR_BASES = [
    "BTC", "ETH", "XRP", "DOGE", "SUI", "ADA", "LINK", "TON",
    "SOL", "BNB", "ZEC", "HYPE", "NEAR", "INJ", "ONDO", "SAGA",
    "TRUMP", "1000PEPE", "PEPE", "TAO", "ENA", "TIA", "WLD",
    "DOT", "LTC", "ARB", "AVAX", "OP", "ATOM", "BCH", "ETC",
]

REQUIRED_PATCH_MARKERS = [
    "F4X_AS5J2I_MAJOR_PRIORITY_SOURCE_SELECTION",
    "F4X_AS5J2I_MAJOR_BASES",
    "F4X_AS5J2I_CVD_MISSING_PRIORITY",
    "F4X_AS5J2I_HIGH_VOLUME_PRIORITY",
    "F4X_AS5J2I_KEEP_HOT_SEEDS",
]

DANGEROUS_CALL_NAMES = {
    "open_position", "create_order", "force_entry", "forceenter",
    "forceenter_pair", "close_all_positions", "force_close_all_positions",
}
DANGEROUS_TRUE_ASSIGN_NAMES = {
    "live_allowed", "risk_up_allowed", "gate_loosen_allowed",
    "forceenter_allowed", "paper_order_allowed", "k_write_allowed", "l_execute_allowed",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def age_sec(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except Exception:
        return None


def as_num(v: Any) -> float | None:
    try:
        if v in (None, "", "None", "null"):
            return None
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return x
    except Exception:
        return None


def norm_pair(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if PAIR_RE.match(s):
        if ":" not in s and s.endswith("/USDT"):
            return s + ":USDT"
        if ":" not in s and s.endswith("/USDC"):
            return s + ":USDC"
        return s
    if SYMBOL_RE.match(s):
        if s.endswith("USDT"):
            return s[:-4] + "/USDT:USDT"
        if s.endswith("USDC"):
            return s[:-4] + "/USDC:USDC"
        if s.endswith("USD"):
            return s[:-3] + "/USD:USD"
    return None


def pair_to_symbol(pair: str) -> str:
    p = norm_pair(pair) or pair.upper()
    base = p.split("/", 1)[0]
    quote = "USDT"
    if "/USDC" in p:
        quote = "USDC"
    elif "/USD" in p:
        quote = "USD"
    return base + quote


def symbol_to_pair(symbol: str) -> str | None:
    return norm_pair(symbol)


def base(pair: str) -> str:
    p = norm_pair(pair) or str(pair).upper()
    return p.split("/", 1)[0]


def is_major(pair: str) -> bool:
    return base(pair) in set(MAJOR_BASES)


def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def extract_pair(d: Any) -> str | None:
    if isinstance(d, str):
        return norm_pair(d)
    if not isinstance(d, dict):
        return None

    for key in ("pair", "symbol", "market", "asset", "order_pair"):
        p = norm_pair(d.get(key))
        if p:
            return p

    for subkey in ("candidate", "raw", "data", "metric", "metrics", "flow", "trigger", "smc", "cvdoi", "cvd_overlay", "sources"):
        sub = d.get(subkey)
        if isinstance(sub, dict):
            p = extract_pair(sub)
            if p:
                return p

    for v in d.values():
        p = norm_pair(v)
        if p:
            return p
    return None


def pick_any_volume(d: Any) -> float | None:
    if not isinstance(d, dict):
        return None

    def nested(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k, v
                yield from nested(v)
        elif isinstance(obj, list):
            for v in obj[:500]:
                yield from nested(v)

    best = None
    for k, v in nested(d):
        if str(k) in VOLUME_KEYS:
            n = as_num(v)
            if n is not None:
                best = max(best or 0.0, abs(n))
    return best


def extract_missing(d: Any) -> list[str]:
    if not isinstance(d, dict):
        return []
    for key in ("missing", "missing_counts", "missing_reasons", "missing_keys", "reasons"):
        v = d.get(key)
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, dict):
            return [str(k) for k, val in v.items() if val]
        if isinstance(v, str) and "MISSING" in v:
            return [v]
    return []


def build_source_map(obj: Any, source_name: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in walk(obj):
        p = extract_pair(rec)
        if not p:
            continue
        slot = out.setdefault(p, {
            "pair": p,
            "sources": [],
            "volume": None,
            "missing": [],
        })
        if source_name not in slot["sources"]:
            slot["sources"].append(source_name)
        vol = pick_any_volume(rec)
        if vol is not None:
            slot["volume"] = max(slot["volume"] or 0.0, vol)
        miss = extract_missing(rec)
        if miss:
            for m in miss:
                if m not in slot["missing"]:
                    slot["missing"].append(m)
    return out


def merge_maps(*maps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for mp in maps:
        for p, r in mp.items():
            slot = out.setdefault(p, {"pair": p, "sources": [], "volume": None, "missing": []})
            for s in r.get("sources", []):
                if s not in slot["sources"]:
                    slot["sources"].append(s)
            vol = r.get("volume")
            if vol is not None:
                slot["volume"] = max(slot["volume"] or 0.0, vol)
            for m in r.get("missing", []):
                if m not in slot["missing"]:
                    slot["missing"].append(m)
    return out


def parse_as5j1_missing_from_full(full_obj: Any, compact_txt: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    for rec in walk(full_obj):
        if not isinstance(rec, dict):
            continue
        p = extract_pair(rec)
        if not p:
            continue
        miss = extract_missing(rec)
        if not miss:
            continue
        rows[p] = {
            "pair": p,
            "missing": miss,
            "volume": pick_any_volume(rec),
        }

    # Compact fallback; many top lines are useful even if full schema changes.
    for line in compact_txt.splitlines():
        if "/USDT" not in line or "missing=" not in line:
            continue
        p = norm_pair(line.split("|", 1)[0].strip())
        if not p:
            continue
        miss = []
        m = re.search(r"missing=(\[[^\]]*\])", line)
        if m:
            try:
                miss = [str(x) for x in ast.literal_eval(m.group(1))]
            except Exception:
                miss = []
        vol = None
        mv = re.search(r"\|vol=([^|]+)", line)
        if mv:
            vol = as_num(mv.group(1))
        if miss:
            rows.setdefault(p, {"pair": p, "missing": miss, "volume": vol})
    return rows


def get_summary(active: Any) -> dict[str, Any]:
    if not isinstance(active, dict):
        return {}
    if isinstance(active.get("new_summary"), dict):
        return active["new_summary"]
    if isinstance(active.get("pair_coverage_summary"), dict):
        return active["pair_coverage_summary"]
    return {}


def get_missing_counts(summary: dict[str, Any]) -> dict[str, int]:
    mc = summary.get("missing_counts") if isinstance(summary, dict) else {}
    out: dict[str, int] = {}
    if isinstance(mc, dict):
        items = mc.items()
    elif isinstance(mc, list):
        items = mc
    else:
        items = []
    for item in items:
        try:
            out[str(item[0])] = int(item[1])
        except Exception:
            pass
    return out


def get_state_counts(summary: dict[str, Any]) -> dict[str, int]:
    sc = summary.get("state_counts") if isinstance(summary, dict) else {}
    out: dict[str, int] = {}
    if isinstance(sc, dict):
        for k, v in sc.items():
            try:
                out[str(k)] = int(v)
            except Exception:
                pass
    return out


def simulate_selection(
    merged: dict[str, dict[str, Any]],
    current_flow_pairs: set[str],
    as5j1_rows: dict[str, dict[str, Any]],
    max_symbols: int,
    min_volume: float,
    hot_limit: int,
    major_limit: int,
    cvd_missing_limit: int,
) -> dict[str, Any]:
    selected: list[str] = []
    reasons: dict[str, str] = {}

    def add_pair(p: str, reason: str) -> None:
        p = norm_pair(p) or p
        if p not in selected:
            selected.append(p)
            reasons[p] = reason

    def vol_of(p: str) -> float:
        v1 = merged.get(p, {}).get("volume")
        v2 = as5j1_rows.get(p, {}).get("volume")
        return float(v1 or v2 or 0.0)

    # 1. Major priority first, but only if seen in source universe.
    major_candidates = [p for p in merged if is_major(p) and (vol_of(p) >= min_volume or p in current_flow_pairs)]
    major_candidates.sort(key=lambda p: (vol_of(p), p), reverse=True)
    for p in major_candidates[:major_limit]:
        add_pair(p, "MAJOR_PRIORITY")

    # 2. Keep current hot CVD seeds, but not all at the expense of missing majors.
    hot = list(current_flow_pairs)
    hot.sort(key=lambda p: (vol_of(p), p), reverse=True)
    for p in hot[:hot_limit]:
        add_pair(p, "KEEP_HOT_FLOW_CONTEXT_SEED")

    # 3. Missing-CVD high volume priority.
    cvd_missing = []
    for p, row in as5j1_rows.items():
        miss = set(row.get("missing") or [])
        if "CVD_MISSING" in miss and vol_of(p) >= min_volume:
            cvd_missing.append(p)
    cvd_missing.sort(key=lambda p: (is_major(p), vol_of(p), p), reverse=True)
    for p in cvd_missing[:cvd_missing_limit]:
        add_pair(p, "CVD_MISSING_PRIORITY")

    # 4. High volume universe fill.
    all_high = [p for p in merged if vol_of(p) >= min_volume]
    all_high.sort(key=lambda p: (vol_of(p), p), reverse=True)
    for p in all_high:
        add_pair(p, "HIGH_VOLUME_PRIORITY")

    # 5. Final fallback: any current flow still not selected.
    for p in hot:
        add_pair(p, "FLOW_CONTEXT_FALLBACK")

    selected = selected[:max_symbols]
    selected_set = set(selected)

    current_flow_missing_after = [p for p in current_flow_pairs if p not in selected_set]
    cvd_missing_selected = [p for p in cvd_missing if p in selected_set]
    cvd_missing_not_selected = [p for p in cvd_missing if p not in selected_set]

    major_status = {}
    for b in MAJOR_BASES:
        p = f"{b}/USDT:USDT"
        major_status[p] = {
            "in_source_universe": p in merged,
            "in_current_flow": p in current_flow_pairs,
            "selected_preview": p in selected_set,
            "volume": vol_of(p),
            "missing": as5j1_rows.get(p, {}).get("missing", []),
            "reason": reasons.get(p),
        }

    return {
        "selected_pairs": selected,
        "selected_symbols": [pair_to_symbol(p) for p in selected],
        "reason_counts": dict(Counter(reasons.get(p, "UNKNOWN") for p in selected)),
        "max_symbols": max_symbols,
        "selected_count": len(selected),
        "current_flow_count": len(current_flow_pairs),
        "current_flow_missing_after": current_flow_missing_after[:80],
        "cvd_missing_total_high_volume": len(cvd_missing),
        "cvd_missing_selected_count": len(cvd_missing_selected),
        "cvd_missing_not_selected_count": len(cvd_missing_not_selected),
        "cvd_missing_selected": cvd_missing_selected[:120],
        "cvd_missing_not_selected": cvd_missing_not_selected[:120],
        "major_status": major_status,
        "top_selected_preview": [
            {
                "pair": p,
                "symbol": pair_to_symbol(p),
                "reason": reasons.get(p),
                "volume": vol_of(p),
                "missing": as5j1_rows.get(p, {}).get("missing", []),
                "in_current_flow": p in current_flow_pairs,
            }
            for p in selected[:120]
        ],
    }


def build_patch_block() -> str:
    majors_json = json.dumps(MAJOR_BASES, indent=4)
    return f'''
# F4X_AS5J2I_MAJOR_PRIORITY_SOURCE_SELECTION
# Preview source-selection policy for bybit_flow_live_collector.py.
# Purpose:
# - Keep hot seeds from existing flow_context/pair_universe.
# - Prioritize major symbols and AS5J1 CVD_MISSING high-volume pairs.
# - Fill remaining slots by high-volume universe.
# Safety:
# - Collector source-selection only.
# - No order, no K/L, no live/risk/gate.
F4X_AS5J2I_MAJOR_BASES = {majors_json}

def _f4x_as5j2i_norm_pair(v):
    import re
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if re.match(r"^[A-Z0-9]{{1,60}}/[A-Z0-9]{{2,20}}(:[A-Z0-9]{{2,20}})?$", s):
        if ":" not in s and s.endswith("/USDT"):
            return s + ":USDT"
        return s
    if re.match(r"^[A-Z0-9]{{1,60}}(USDT|USDC|USD|PERP)$", s):
        if s.endswith("USDT"):
            return s[:-4] + "/USDT:USDT"
        if s.endswith("USDC"):
            return s[:-4] + "/USDC:USDC"
        if s.endswith("USD"):
            return s[:-3] + "/USD:USD"
    return None

def _f4x_as5j2i_pair_to_symbol(pair):
    p = _f4x_as5j2i_norm_pair(pair) or str(pair).upper()
    b = p.split("/", 1)[0]
    q = "USDT"
    if "/USDC" in p:
        q = "USDC"
    elif "/USD" in p:
        q = "USD"
    return b + q

def _f4x_as5j2i_runtime_dir():
    import os
    from pathlib import Path
    rt = os.environ.get("REVO_RUNTIME_DIR") or globals().get("RUNTIME_DIR")
    if rt:
        return Path(rt)
    return Path("user_data/revo_alpha/runtime/bybit")

def _f4x_as5j2i_load_json(path):
    import json
    from pathlib import Path
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(errors="replace"))
    except Exception:
        return None
    return None

def _f4x_as5j2i_walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _f4x_as5j2i_walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _f4x_as5j2i_walk(v)

def _f4x_as5j2i_extract_pair(d):
    if isinstance(d, str):
        return _f4x_as5j2i_norm_pair(d)
    if not isinstance(d, dict):
        return None
    for k in ("pair", "symbol", "market", "asset", "order_pair"):
        p = _f4x_as5j2i_norm_pair(d.get(k))
        if p:
            return p
    for v in d.values():
        p = _f4x_as5j2i_norm_pair(v)
        if p:
            return p
    return None

def _f4x_as5j2i_volume(d):
    keys = {{
        "quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd",
        "turnover24h", "quoteVolume24h", "volume24h", "vol_usd",
        "volume", "volume_24h", "vol24h"
    }}
    best = None
    def rec(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys:
                    try:
                        x = abs(float(v))
                        nonlocal_best[0] = max(nonlocal_best[0] or 0.0, x)
                    except Exception:
                        pass
                rec(v)
        elif isinstance(o, list):
            for v in o[:500]:
                rec(v)
    nonlocal_best = [best]
    rec(d)
    return nonlocal_best[0]

def _f4x_as5j2i_collect_pairs(obj, source_name, out):
    for d in _f4x_as5j2i_walk(obj):
        p = _f4x_as5j2i_extract_pair(d)
        if not p:
            continue
        slot = out.setdefault(p, {{"sources": [], "volume": 0.0, "missing": []}})
        if source_name not in slot["sources"]:
            slot["sources"].append(source_name)
        v = _f4x_as5j2i_volume(d)
        if v:
            slot["volume"] = max(float(slot.get("volume") or 0.0), float(v))
        if isinstance(d, dict):
            for key in ("missing", "missing_reasons", "missing_keys", "reasons"):
                mv = d.get(key)
                if isinstance(mv, list):
                    for m in mv:
                        if str(m) not in slot["missing"]:
                            slot["missing"].append(str(m))

def choose_symbols():
    # F4X_AS5J2I_CVD_MISSING_PRIORITY
    # F4X_AS5J2I_HIGH_VOLUME_PRIORITY
    # F4X_AS5J2I_KEEP_HOT_SEEDS
    import os

    rt = _f4x_as5j2i_runtime_dir()
    max_symbols = int(os.environ.get("BYBIT_COLLECTOR_MAX_SYMBOLS", globals().get("MAX_SYMBOLS_PER_CYCLE", globals().get("TOP_N", 120))))
    hot_limit = int(os.environ.get("BYBIT_COLLECTOR_HOT_LIMIT", 40))
    major_limit = int(os.environ.get("BYBIT_COLLECTOR_MAJOR_LIMIT", 40))
    cvd_missing_limit = int(os.environ.get("BYBIT_COLLECTOR_CVD_MISSING_LIMIT", 80))
    min_volume = float(os.environ.get("BYBIT_COLLECTOR_MIN_VOLUME_USD", 4000000))

    src = {{}}
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
        obj = _f4x_as5j2i_load_json(path)
        if obj is not None:
            _f4x_as5j2i_collect_pairs(obj, name, src)

    def vol(p):
        return float(src.get(p, {{}}).get("volume") or 0.0)

    selected = []
    selected_set = set()

    def add(p):
        p = _f4x_as5j2i_norm_pair(p)
        if not p or p in selected_set:
            return
        selected_set.add(p)
        selected.append(p)

    current_flow = [p for p, r in src.items() if "flow_context" in r.get("sources", [])]
    current_flow.sort(key=lambda p: (vol(p), p), reverse=True)

    major_pairs = [p for p in src if p.split("/", 1)[0] in F4X_AS5J2I_MAJOR_BASES and (vol(p) >= min_volume or p in current_flow)]
    major_pairs.sort(key=lambda p: (vol(p), p), reverse=True)
    for p in major_pairs[:major_limit]:
        add(p)

    for p in current_flow[:hot_limit]:
        add(p)

    cvd_missing = [
        p for p, r in src.items()
        if "CVD_MISSING" in r.get("missing", []) and vol(p) >= min_volume
    ]
    cvd_missing.sort(key=lambda p: ((p.split("/", 1)[0] in F4X_AS5J2I_MAJOR_BASES), vol(p), p), reverse=True)
    for p in cvd_missing[:cvd_missing_limit]:
        add(p)

    high_volume = [p for p in src if vol(p) >= min_volume]
    high_volume.sort(key=lambda p: (vol(p), p), reverse=True)
    for p in high_volume:
        add(p)

    for p in current_flow:
        add(p)

    return [_f4x_as5j2i_pair_to_symbol(p) for p in selected[:max_symbols]]
'''


def replace_choose_symbols(src: str) -> tuple[str, list[str]]:
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^def\s+choose_symbols\s*\(", line):
            start = i
            break
    if start is None:
        raise RuntimeError("choose_symbols function not found")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.startswith("def ") or line.startswith("class ") or line.startswith("if __name__"):
            end = j
            break

    block = build_patch_block().strip("\n").splitlines()
    new_lines = lines[:start] + block + lines[end:]
    return "\n".join(new_lines) + "\n", [f"REPLACED_choose_symbols_LINES_{start+1}_{end}"]


def py_compile(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["python3", "-m", "py_compile", str(path)],
        text=True,
        capture_output=True,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def ast_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = ast_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def smart_forbidden_scan(src: str) -> dict[str, Any]:
    hits = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "hits": [{"type": "syntax_error", "detail": str(e)}]}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast_call_name(node.func) or ""
            short = name.rsplit(".", 1)[-1]
            if short in DANGEROUS_CALL_NAMES:
                hits.append({"type": "dangerous_call", "name": name, "lineno": getattr(node, "lineno", None)})
            if name == "subprocess.Popen":
                hits.append({"type": "subprocess_popen", "name": name, "lineno": getattr(node, "lineno", None)})

        if isinstance(node, ast.Assign):
            val_true = isinstance(node.value, ast.Constant) and node.value is True
            if val_true:
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in DANGEROUS_TRUE_ASSIGN_NAMES:
                        hits.append({"type": "dangerous_true_assignment", "name": t.id, "lineno": getattr(node, "lineno", None)})

        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                try:
                    key = ast.literal_eval(k) if k is not None else None
                except Exception:
                    key = None
                if key in DANGEROUS_TRUE_ASSIGN_NAMES and isinstance(v, ast.Constant) and v.value is True:
                    hits.append({"type": "dangerous_true_dict_value", "name": key, "lineno": getattr(node, "lineno", None)})

    return {"ok": len(hits) == 0, "hits": hits}


def inspect_k_l(runtime: Path) -> dict[str, Any]:
    k = read_json(runtime / K_FILE, {})
    l = read_json(runtime / L_FILE, {})
    k_clean = isinstance(k, dict) and not k.get("has_order_intent") and int(k.get("intent_count") or 0) == 0 and not k.get("order_intents")
    l_clean = not isinstance(l, dict) or str(l.get("decision") or "NO_VALID_ORDER_INTENT") in {"NO_VALID_ORDER_INTENT", "HOLD", ""}
    return {
        "k_clean": k_clean,
        "k_mode": k.get("mode") if isinstance(k, dict) else None,
        "k_intent_count": k.get("intent_count") if isinstance(k, dict) else None,
        "k_has_order_intent": k.get("has_order_intent") if isinstance(k, dict) else None,
        "l_clean": l_clean,
        "l_decision": l.get("decision") if isinstance(l, dict) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--min-volume-usd", type=float, default=4000000.0)
    ap.add_argument("--max-symbols", type=int, default=120)
    ap.add_argument("--hot-limit", type=int, default=40)
    ap.add_argument("--major-limit", type=int, default=40)
    ap.add_argument("--cvd-missing-limit", type=int, default=80)
    ap.add_argument("--top-n", type=int, default=80)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    warnings: list[str] = []

    collector = repo / COLLECTOR_REL
    collector_src = read_text(collector)
    if not collector_src:
        failures.append("COLLECTOR_SOURCE_MISSING_OR_EMPTY")

    files = {
        "as5j1_active": runtime / AS5J1_ACTIVE,
        "as5j1_full": runtime / AS5J1_FULL,
        "as5j1_compact": runtime / AS5J1_COMPACT,
        "flow_context": runtime / FLOW_CONTEXT,
        "f3a_b": runtime / F3A_B,
        "f3b": runtime / F3B,
        "feeder_raw": runtime / FEEDER_RAW,
        "feeder_lanes": runtime / FEEDER_LANES,
        "pair_universe": runtime / PAIR_UNIVERSE,
        "full": runtime / FULL,
        "paper": runtime / PAPER,
    }

    as5j1_active = read_json(files["as5j1_active"], {}) or {}
    as5j1_full = read_json(files["as5j1_full"], {}) or {}
    as5j1_compact = read_text(files["as5j1_compact"])
    official_summary = get_summary(as5j1_active)
    official_missing_counts = get_missing_counts(official_summary)
    official_state_counts = get_state_counts(official_summary)

    source_maps = {}
    for name in ["flow_context", "f3a_b", "f3b", "feeder_raw", "feeder_lanes", "pair_universe", "full", "paper"]:
        source_maps[name] = build_source_map(read_json(files[name], {}) or {}, name)

    merged = merge_maps(*source_maps.values())
    flow_pairs = set(source_maps["flow_context"].keys())
    as5j1_rows = parse_as5j1_missing_from_full(as5j1_full, as5j1_compact)

    # Enrich AS5J1 missing rows volume from merged map.
    for p, r in as5j1_rows.items():
        if r.get("volume") is None:
            r["volume"] = merged.get(p, {}).get("volume")

    simulation = simulate_selection(
        merged=merged,
        current_flow_pairs=flow_pairs,
        as5j1_rows=as5j1_rows,
        max_symbols=args.max_symbols,
        min_volume=args.min_volume_usd,
        hot_limit=args.hot_limit,
        major_limit=args.major_limit,
        cvd_missing_limit=args.cvd_missing_limit,
    )

    candidate_source = runtime / "F4X_AS5J2I_BYBIT_FLOW_COLLECTOR_MAJOR_PRIORITY_CANDIDATE_SOURCE_PREVIEW.py"
    diff_path = runtime / "F4X_AS5J2I_BYBIT_FLOW_COLLECTOR_MAJOR_PRIORITY_PATCH_PREVIEW.diff"

    patch_notes = []
    candidate_src = ""
    diff_lines: list[str] = []

    if not failures:
        try:
            candidate_src, patch_notes = replace_choose_symbols(collector_src)
            candidate_source.write_text(candidate_src, encoding="utf-8")
            diff_lines = list(difflib.unified_diff(
                collector_src.splitlines(),
                candidate_src.splitlines(),
                fromfile=COLLECTOR_REL,
                tofile=COLLECTOR_REL + ".AS5J2I_MAJOR_PRIORITY_CANDIDATE",
                lineterm="",
            ))
            diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")
        except Exception as e:
            failures.append(f"PATCH_PREVIEW_BUILD_FAILED:{type(e).__name__}:{e}")
            diff_path.write_text("", encoding="utf-8")
    else:
        diff_path.write_text("", encoding="utf-8")

    compile_result = py_compile(candidate_source) if candidate_source.exists() else {"ok": False, "stderr": "candidate missing"}
    if not compile_result.get("ok"):
        failures.append("CANDIDATE_COMPILE_FAILED")

    markers = {m: (m in candidate_src) for m in REQUIRED_PATCH_MARKERS}
    missing_markers = [k for k, v in markers.items() if not v]
    if missing_markers:
        failures.append("CANDIDATE_PATCH_MARKERS_MISSING:" + ",".join(missing_markers))

    forbidden_scan = smart_forbidden_scan(candidate_src)
    if not forbidden_scan.get("ok"):
        failures.append("CANDIDATE_SMART_FORBIDDEN_SCAN_FAILED")

    k_l_state = inspect_k_l(runtime)
    if not k_l_state.get("k_clean"):
        warnings.append("K_NOT_CLEAN")
    if not k_l_state.get("l_clean"):
        warnings.append("L_NOT_CLEAN")

    major_selected = sum(1 for st in simulation["major_status"].values() if st.get("selected_preview"))
    major_in_source = sum(1 for st in simulation["major_status"].values() if st.get("in_source_universe"))
    cvd_missing_selected = int(simulation["cvd_missing_selected_count"])
    cvd_missing_total = int(simulation["cvd_missing_total_high_volume"])

    if cvd_missing_total > 0 and cvd_missing_selected == 0:
        warnings.append("NO_CVD_MISSING_HIGH_VOLUME_SELECTED_IN_SIMULATION")
    if major_in_source > 0 and major_selected < max(1, min(10, major_in_source)):
        warnings.append(f"MAJOR_SELECTED_LOW:{major_selected}/{major_in_source}")

    if failures:
        final_decision = "F4X_AS5J2I_PATCH_PREVIEW_FAILED_REVIEW_REQUIRED"
        next_action = "Do not execute collector patch. Review failures and candidate diff."
    else:
        final_decision = "F4X_AS5J2I_MAJOR_PRIORITY_PATCH_PREVIEW_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "Candidate collector source compiles and selection simulation prioritizes majors/CVD-missing pairs. Next execute backup+compile only; no restart yet."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": MODE,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "source_overwrite_allowed": False,
        "collector_restart_allowed": False,
        "runpaper_restart_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "k_l_state": k_l_state,
        "official_as5j1_summary": {
            "final_decision": as5j1_active.get("final_decision") if isinstance(as5j1_active, dict) else None,
            "state_counts": official_state_counts,
            "missing_counts": official_missing_counts,
        },
        "file_ages_sec": {name: age_sec(path) for name, path in files.items()},
        "source_pair_counts": {name: len(mp) for name, mp in source_maps.items()},
        "source_volume_ok_counts": {
            name: sum(1 for r in mp.values() if float(r.get("volume") or 0.0) >= args.min_volume_usd)
            for name, mp in source_maps.items()
        },
        "flow_context_pair_count": len(flow_pairs),
        "merged_pair_count": len(merged),
        "as5j1_missing_row_count": len(as5j1_rows),
        "simulation": simulation,
        "patch_preview": {
            "collector": str(collector),
            "candidate_source": str(candidate_source),
            "diff": str(diff_path),
            "diff_line_count": len(diff_lines),
            "patch_notes": patch_notes,
            "compile": compile_result,
            "markers": markers,
            "missing_markers": missing_markers,
            "smart_forbidden_scan": forbidden_scan,
        },
        "decision_policy": [
            "AS5J2I is patch-preview only.",
            "AS5J2I does not overwrite collector source.",
            "AS5J2I does not restart collector.",
            "AS5J2I does not restart runpaper.",
            "AS5J2I does not write K.",
            "AS5J2I does not execute L.",
            "AS5J2I does not create paper order.",
            "AS5J2I does not enable live, risk-up, or gate-loosen.",
            "Actual source overwrite requires separate AS5J2I1 approval.",
            "Actual collector Docker recreate/restart requires separate AS5J2I2 approval.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"

    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5J2I_REMAINING_CVD_COVERAGE_AND_MAJOR_PRIORITY_PATCH_PREVIEW_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "source_overwrite=HOLD",
        "collector_restart=HOLD",
        "runpaper_restart=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        f"next_action={next_action}",
        "FAILURES",
        *(failures if failures else ["NONE"]),
        "WARNINGS",
        *(warnings if warnings else ["NONE"]),
        "K_L_STATE",
        str(k_l_state),
        "OFFICIAL_AS5J1_SUMMARY",
        f"official_final_decision={result['official_as5j1_summary']['final_decision']}",
        f"state_counts={official_state_counts}",
        f"missing_counts={official_missing_counts}",
        "SOURCE_COUNTS",
        f"source_pair_counts={result['source_pair_counts']}",
        f"source_volume_ok_counts={result['source_volume_ok_counts']}",
        f"flow_context_pair_count={len(flow_pairs)}",
        f"merged_pair_count={len(merged)}",
        f"as5j1_missing_row_count={len(as5j1_rows)}",
        "SIMULATION",
        f"max_symbols={simulation['max_symbols']}",
        f"selected_count={simulation['selected_count']}",
        f"reason_counts={simulation['reason_counts']}",
        f"cvd_missing_total_high_volume={simulation['cvd_missing_total_high_volume']}",
        f"cvd_missing_selected_count={simulation['cvd_missing_selected_count']}",
        f"cvd_missing_not_selected_count={simulation['cvd_missing_not_selected_count']}",
        "MAJOR_STATUS",
    ]

    for p, st in simulation["major_status"].items():
        lines.append(
            f"{p}|in_source={st['in_source_universe']}|in_current_flow={st['in_current_flow']}|"
            f"selected={st['selected_preview']}|vol={st['volume']}|missing={st['missing']}|reason={st['reason']}"
        )

    lines.extend([
        "TOP_SELECTED_PREVIEW",
    ])
    for row in simulation["top_selected_preview"][:args.top_n]:
        lines.append(
            f"{row['pair']}|symbol={row['symbol']}|reason={row['reason']}|vol={row['volume']}|"
            f"missing={row['missing']}|in_current_flow={row['in_current_flow']}"
        )

    lines.extend([
        "CVD_MISSING_SELECTED_SAMPLE",
        *simulation["cvd_missing_selected"][:args.top_n],
        "CVD_MISSING_NOT_SELECTED_SAMPLE",
        *simulation["cvd_missing_not_selected"][:args.top_n],
        "PATCH_PREVIEW",
        f"collector={collector}",
        f"candidate_source={candidate_source}",
        f"diff={diff_path}",
        f"diff_line_count={len(diff_lines)}",
        f"patch_notes={patch_notes}",
        "COMPILE",
        f"candidate_compile_ok={compile_result.get('ok')}|stderr={str(compile_result.get('stderr', '')).strip()}",
        "MARKERS",
        f"markers={markers}",
        f"missing_markers={missing_markers}",
        "SMART_FORBIDDEN_SCAN",
        f"ok={forbidden_scan.get('ok')}|hits={forbidden_scan.get('hits')}",
        "DIFF_HEAD",
        *diff_lines[:260],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"candidate_source={candidate_source}",
        f"diff={diff_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ])

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
