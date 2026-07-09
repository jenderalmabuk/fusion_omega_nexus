#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import difflib
import importlib.util
import inspect
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2I0_MAJOR_PRIORITY_PATCH_PREVIEW_V2_SIGNATURE_AND_SYMBOL_FIX_ONLY"
MODE = "MAJOR_PRIORITY_PATCH_PREVIEW_V2_SIGNATURE_AND_SYMBOL_FIX_ONLY"

COLLECTOR_REL = "scripts/bybit_flow_live_collector.py"
AS5J2I_ACTIVE = "F4X_AS5J2I_REMAINING_CVD_COVERAGE_AND_MAJOR_PRIORITY_PATCH_PREVIEW_ONLY_ACTIVE.json"

K_FILE = "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
L_FILE = "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"

MAJOR_BASES = [
    "BTC", "ETH", "XRP", "DOGE", "SUI", "ADA", "LINK", "TON",
    "SOL", "BNB", "ZEC", "HYPE", "NEAR", "INJ", "ONDO", "SAGA",
    "TRUMP", "1000PEPE", "PEPE", "TAO", "ENA", "TIA", "WLD",
    "DOT", "LTC", "ARB", "AVAX", "OP", "ATOM", "BCH", "ETC",
]

REQUIRED_MARKERS = [
    "F4X_AS5J2I0_MAJOR_PRIORITY_SOURCE_SELECTION_V2",
    "F4X_AS5J2I0_PRESERVE_CHOOSE_SYMBOLS_SIGNATURE",
    "F4X_AS5J2I0_FIX_USDT_SYMBOL_CONVERSION",
    "F4X_AS5J2I0_CVD_MISSING_PRIORITY",
    "F4X_AS5J2I0_TICKER_COLD_FALLBACK",
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


def build_patch_block() -> str:
    majors_json = json.dumps(MAJOR_BASES, indent=4)
    return f'''
# F4X_AS5J2I0_MAJOR_PRIORITY_SOURCE_SELECTION_V2
# F4X_AS5J2I0 fixes AS5J2I v1:
# - preserves choose_symbols(tickers) signature
# - fixes USDT symbol conversion, e.g. BTC/USDT:USDT -> BTCUSDT, not BTCUSD
# - keeps ticker24h cold fallback
# Safety: collector source-selection only; no K/L/order/live/risk/gate.
F4X_AS5J2I0_MAJOR_BASES = {majors_json}

def _f4x_as5j2i0_norm_pair(v):
    import re
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if re.match(r"^[A-Z0-9]{{1,60}}/[A-Z0-9]{{2,20}}(:[A-Z0-9]{{2,20}})?$", s):
        if ":" not in s and s.endswith("/USDT"):
            return s + ":USDT"
        if ":" not in s and s.endswith("/USDC"):
            return s + ":USDC"
        return s
    if re.match(r"^[A-Z0-9]{{1,60}}(USDT|USDC|USD|PERP)$", s):
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
    keys = {{
        "quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd",
        "turnover24h", "quoteVolume24h", "volume24h", "vol_usd",
        "volume", "volume_24h", "vol24h"
    }}

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

        slot = out.setdefault(p, {{"sources": [], "volume": 0.0, "missing": []}})
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

        slot = out.setdefault(p, {{"sources": [], "volume": 0.0, "missing": []}})
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
        obj = _f4x_as5j2i0_load_json(path)
        if obj is not None:
            _f4x_as5j2i0_collect_pairs(obj, name, src)

    _f4x_as5j2i0_add_tickers(tickers, src)

    def vol(p):
        return float(src.get(p, {{}}).get("volume") or 0.0)

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
    return "\n".join(new_lines) + "\n", [f"REPLACED_choose_symbols_LINES_{start + 1}_{end}_WITH_V2_SIGNATURE_AND_SYMBOL_FIX"]


def import_candidate_and_smoke(candidate: Path, runtime: Path) -> dict[str, Any]:
    old_runtime = os.environ.get("REVO_RUNTIME_DIR")
    old_max = os.environ.get("BYBIT_COLLECTOR_MAX_SYMBOLS")

    os.environ["REVO_RUNTIME_DIR"] = str(runtime)
    os.environ["BYBIT_COLLECTOR_MAX_SYMBOLS"] = "120"

    try:
        spec = importlib.util.spec_from_file_location("as5j2i0_candidate", str(candidate))
        if spec is None or spec.loader is None:
            return {"ok": False, "errors": ["IMPORT_SPEC_FAILED"]}

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        sig = inspect.signature(mod.choose_symbols)
        params = list(sig.parameters.keys())

        sample_tickers = [
            {"symbol": "BTCUSDT", "turnover24h": "9999999999"},
            {"symbol": "ETHUSDT", "turnover24h": "8888888888"},
            {"symbol": "DOGEUSDT", "turnover24h": "7777777777"},
            {"symbol": "SUIUSDT", "turnover24h": "666666666"},
            {"symbol": "ADAUSDT", "turnover24h": "555555555"},
            {"symbol": "LINKUSDT", "turnover24h": "444444444"},
            {"symbol": "TONUSDT", "turnover24h": "333333333"},
            {"symbol": "1000PEPEUSDT", "turnover24h": "222222222"},
        ]

        errors = []

        if len(params) != 1:
            errors.append(f"SIGNATURE_PARAM_COUNT_NOT_ONE:{params}")

        try:
            empty_result = mod.choose_symbols([])
        except TypeError as e:
            return {"ok": False, "errors": [f"TYPEERROR_EMPTY:{e}"], "signature": str(sig)}

        try:
            sample_result = mod.choose_symbols(sample_tickers)
        except TypeError as e:
            return {"ok": False, "errors": [f"TYPEERROR_SAMPLE:{e}"], "signature": str(sig)}

        if not isinstance(empty_result, list):
            errors.append("EMPTY_RESULT_NOT_LIST")
        if not isinstance(sample_result, list):
            errors.append("SAMPLE_RESULT_NOT_LIST")

        bad_symbols = [
            s for s in sample_result
            if not isinstance(s, str) or not s.endswith("USDT") or s.endswith("USD")
        ]
        if bad_symbols:
            errors.append("BAD_SYMBOL_SUFFIX:" + ",".join(map(str, bad_symbols[:20])))

        required_symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "SUIUSDT", "ADAUSDT", "LINKUSDT", "TONUSDT"]
        missing_required = [s for s in required_symbols if s not in sample_result]
        if missing_required:
            errors.append("REQUIRED_MAJOR_SYMBOLS_NOT_SELECTED:" + ",".join(missing_required))

        legacy_wrong = [s for s in sample_result if s in {"BTCUSD", "ETHUSD", "DOGEUSD", "SUIUSD", "ADAUSD", "LINKUSD", "TONUSD"}]
        if legacy_wrong:
            errors.append("LEGACY_USD_SYMBOL_BUG_PRESENT:" + ",".join(legacy_wrong))

        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "signature": str(sig),
            "signature_params": params,
            "empty_count": len(empty_result),
            "sample_count": len(sample_result),
            "sample_head": sample_result[:80],
            "required_symbols": required_symbols,
            "missing_required": missing_required,
            "legacy_wrong_symbols": legacy_wrong,
            "bad_symbols": bad_symbols,
        }
    except Exception as e:
        return {"ok": False, "errors": [f"SMOKE_EXCEPTION:{type(e).__name__}:{e}"]}
    finally:
        if old_runtime is None:
            os.environ.pop("REVO_RUNTIME_DIR", None)
        else:
            os.environ["REVO_RUNTIME_DIR"] = old_runtime

        if old_max is None:
            os.environ.pop("BYBIT_COLLECTOR_MAX_SYMBOLS", None)
        else:
            os.environ["BYBIT_COLLECTOR_MAX_SYMBOLS"] = old_max


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
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

    as5j2i = read_json(runtime / AS5J2I_ACTIVE, {}) or {}
    as5j2i_decision = as5j2i.get("final_decision") if isinstance(as5j2i, dict) else None
    if as5j2i_decision != "F4X_AS5J2I_MAJOR_PRIORITY_PATCH_PREVIEW_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY":
        warnings.append(f"AS5J2I_FINAL_DECISION_NOT_READY:{as5j2i_decision}")

    candidate_source = runtime / "F4X_AS5J2I0_BYBIT_FLOW_COLLECTOR_MAJOR_PRIORITY_V2_CANDIDATE_SOURCE_PREVIEW.py"
    diff_path = runtime / "F4X_AS5J2I0_BYBIT_FLOW_COLLECTOR_MAJOR_PRIORITY_V2_PATCH_PREVIEW.diff"

    candidate_src = ""
    diff_lines: list[str] = []
    patch_notes: list[str] = []

    if not failures:
        try:
            candidate_src, patch_notes = replace_choose_symbols(collector_src)
            candidate_source.write_text(candidate_src, encoding="utf-8")

            diff_lines = list(difflib.unified_diff(
                collector_src.splitlines(),
                candidate_src.splitlines(),
                fromfile=COLLECTOR_REL,
                tofile=COLLECTOR_REL + ".AS5J2I0_V2_SIGNATURE_SYMBOL_FIX_CANDIDATE",
                lineterm="",
            ))
            diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")
        except Exception as e:
            failures.append(f"PATCH_BUILD_FAILED:{type(e).__name__}:{e}")
            diff_path.write_text("", encoding="utf-8")
    else:
        diff_path.write_text("", encoding="utf-8")

    compile_result = py_compile(candidate_source) if candidate_source.exists() else {"ok": False, "stderr": "candidate missing"}
    if not compile_result.get("ok"):
        failures.append("CANDIDATE_COMPILE_FAILED")

    markers = {m: (m in candidate_src) for m in REQUIRED_MARKERS}
    missing_markers = [k for k, v in markers.items() if not v]
    if missing_markers:
        failures.append("CANDIDATE_MARKERS_MISSING:" + ",".join(missing_markers))

    forbidden_scan = smart_forbidden_scan(candidate_src)
    if not forbidden_scan.get("ok"):
        failures.append("CANDIDATE_SMART_FORBIDDEN_SCAN_FAILED")

    smoke = import_candidate_and_smoke(candidate_source, runtime) if candidate_source.exists() else {"ok": False, "errors": ["candidate missing"]}
    if not smoke.get("ok"):
        failures.append("CANDIDATE_SMOKE_TEST_FAILED:" + ";".join(smoke.get("errors", [])))

    k_l_state = inspect_k_l(runtime)
    if not k_l_state.get("k_clean"):
        warnings.append("K_NOT_CLEAN")
    if not k_l_state.get("l_clean"):
        warnings.append("L_NOT_CLEAN")

    if failures:
        final_decision = "F4X_AS5J2I0_MAJOR_PRIORITY_V2_PATCH_PREVIEW_FAILED_REVIEW_REQUIRED"
        next_action = "Do not execute collector patch. Review failures, smoke test, and diff."
    else:
        final_decision = "F4X_AS5J2I0_MAJOR_PRIORITY_V2_SIGNATURE_SYMBOL_FIX_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "V2 candidate preserves choose_symbols(tickers), emits USDT symbols, compiles, and smoke test passes. Next execute backup+compile only; no restart yet."

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
        "as5j2i_final_decision": as5j2i_decision,
        "k_l_state": k_l_state,
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
            "smoke_test": smoke,
        },
        "decision_policy": [
            "AS5J2I0 is patch-preview V2 only.",
            "AS5J2I0 does not overwrite collector source.",
            "AS5J2I0 does not restart collector.",
            "AS5J2I0 preserves choose_symbols(tickers) signature.",
            "AS5J2I0 fixes USDT symbol conversion.",
            "AS5J2I0 smoke-tests candidate import and choose_symbols calls.",
            "AS5J2I0 does not write K.",
            "AS5J2I0 does not execute L.",
            "AS5J2I0 does not create paper order.",
            "AS5J2I0 does not enable live, risk-up, or gate-loosen.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"

    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5J2I0_MAJOR_PRIORITY_PATCH_PREVIEW_V2_SIGNATURE_AND_SYMBOL_FIX_ONLY_COMPACT",
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
        "AS5J2I",
        f"as5j2i_final_decision={as5j2i_decision}",
        "K_L_STATE",
        str(k_l_state),
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
        "SMOKE_TEST",
        f"ok={smoke.get('ok')}",
        f"errors={smoke.get('errors')}",
        f"signature={smoke.get('signature')}",
        f"signature_params={smoke.get('signature_params')}",
        f"empty_count={smoke.get('empty_count')}",
        f"sample_count={smoke.get('sample_count')}",
        f"required_symbols={smoke.get('required_symbols')}",
        f"missing_required={smoke.get('missing_required')}",
        f"legacy_wrong_symbols={smoke.get('legacy_wrong_symbols')}",
        f"bad_symbols={smoke.get('bad_symbols')}",
        f"sample_head={smoke.get('sample_head')}",
        "DIFF_HEAD",
        *diff_lines[:280],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"candidate_source={candidate_source}",
        f"diff={diff_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ]

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
