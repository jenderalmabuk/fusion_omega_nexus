#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    text = str(v).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = text.replace(" UTC", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    return None


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def bybit_symbol(pair: str) -> str:
    base = pair.split("/")[0]
    return f"{base}USDT"


def resolve_candidate_time(runtime: Path, candidate: Dict[str, Any]) -> Tuple[Optional[datetime], str, Dict[str, Any]]:
    pair = norm(candidate.get("pair"))
    side = norm(candidate.get("side")).upper()
    setup_state = norm(candidate.get("setup_state"))

    direct = parse_dt(candidate.get("candle") or candidate.get("ts") or candidate.get("candidate_candle"))
    if direct:
        return direct, "DIRECT_CANDIDATE_FIELD", candidate

    sources = [
        ("F2U_SETUP_STATE", runtime / "revo_f2u_setup_state_events.jsonl"),
        ("GATE_SHADOW", runtime / "revo_gate_shadow_events.jsonl"),
        ("GATE_HEARTBEAT", runtime / "revo_gate_heartbeat_events.jsonl"),
    ]

    best_dt = None
    best_source = "NO_TIMESTAMP_FOUND"
    best_row: Dict[str, Any] = {}

    for source_name, path in sources:
        for row in read_jsonl(path):
            if norm(row.get("pair")) != pair:
                continue

            row_side = norm(row.get("side")).upper()
            if row_side not in {"UNKNOWN", "NA"} and row_side != side:
                continue

            row_state = norm(row.get("setup_state"))
            if source_name == "F2U_SETUP_STATE" and row_state != "UNKNOWN" and setup_state != "UNKNOWN":
                if row_state != setup_state:
                    continue

            dt = parse_dt(row.get("candle") or row.get("ts") or row.get("generated_at"))
            if not dt:
                continue

            if best_dt is None or dt > best_dt:
                best_dt = dt
                best_source = source_name
                best_row = row

    return best_dt, best_source, best_row


def fetch_bybit_klines(symbol: str, start_ms: int, limit: int = 20) -> List[List[Any]]:
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "5",
        "start": str(start_ms),
        "limit": str(limit),
    }
    url = "https://api.bybit.com/v5/market/kline?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "F2X-B-shadow-outcome-audit"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if str(data.get("retCode")) != "0":
        raise RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")

    rows = data.get("result", {}).get("list", []) or []
    return sorted(rows, key=lambda r: int(r[0]))


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def calc_outcome(side: str, rows: List[List[Any]], horizon: int) -> Dict[str, Any]:
    if len(rows) < 2:
        return {"status": "NOT_ENOUGH_CANDLES"}

    entry = float(rows[0][4])
    future = rows[1:1 + horizon]

    if not future:
        return {"status": "NO_FUTURE_CANDLE"}

    highs = [float(r[2]) for r in future]
    lows = [float(r[3]) for r in future]
    closes = [float(r[4]) for r in future]

    if side == "LONG":
        mfe = pct(max(highs), entry)
        mae = pct(min(lows), entry)
        close_ret = pct(closes[-1], entry)
    else:
        mfe = pct(entry, min(lows))
        mae = pct(entry, max(highs))
        close_ret = pct(entry, closes[-1])

    return {
        "status": "OK",
        "entry_ref_close": entry,
        "horizon_candles": horizon,
        "future_candles": len(future),
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "close_return_pct": round(close_ret, 4),
        "hit_pos_03": int(mfe >= 0.3),
        "hit_pos_05": int(mfe >= 0.5),
        "hit_neg_03": int(mae <= -0.3),
        "hit_neg_05": int(mae <= -0.5),
    }


def aggregate(results: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    rows = [x[key] for x in results if x.get(key, {}).get("status") == "OK"]
    if not rows:
        return {"count": 0}

    return {
        "count": len(rows),
        "avg_mfe_pct": round(sum(x["mfe_pct"] for x in rows) / len(rows), 4),
        "avg_mae_pct": round(sum(x["mae_pct"] for x in rows) / len(rows), 4),
        "avg_close_return_pct": round(sum(x["close_return_pct"] for x in rows) / len(rows), 4),
        "hit_pos_03": sum(x["hit_pos_03"] for x in rows),
        "hit_pos_05": sum(x["hit_pos_05"] for x in rows),
        "hit_neg_03": sum(x["hit_neg_03"] for x in rows),
        "hit_neg_05": sum(x["hit_neg_05"] for x in rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    src = runtime / "revo_f2w_b_trigger_field_score_state.json"
    out_json = runtime / "revo_f2x_shadow_outcome_state.json"
    out_compact_runtime = runtime / "F2X_SHADOW_OUTCOME_TRACKER_COMPACT.txt"
    out_compact_root = Path("F2X_SHADOW_OUTCOME_TRACKER_COMPACT.txt")

    data = read_json(src, {})
    rows = data.get("rows", []) if isinstance(data, dict) else []

    candidates = [
        r for r in rows
        if r.get("f2w_b_trigger_status") == "TRIGGER_CONFIRMED_SHADOW"
    ]

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for r in candidates:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        dt, ts_source, ts_row = resolve_candidate_time(runtime, r)

        if dt is None:
            errors.append({
                "pair": pair,
                "side": side,
                "error": "TIMESTAMP_RESOLVE_FAIL",
                "candidate_keys": sorted(r.keys()),
            })
            continue

        symbol = bybit_symbol(pair)
        start_ms = int(dt.timestamp() * 1000)

        try:
            klines = fetch_bybit_klines(symbol, start_ms, limit=args.limit)
            time.sleep(0.35)
        except Exception as e:
            errors.append({
                "pair": pair,
                "side": side,
                "symbol": symbol,
                "timestamp": dt.isoformat(),
                "timestamp_source": ts_source,
                "error": repr(e),
            })
            continue

        item = {
            "pair": pair,
            "symbol": symbol,
            "side": side,
            "candidate_ts": dt.isoformat(),
            "timestamp_source": ts_source,
            "setup_state": r.get("setup_state"),
            "trigger_ratio": r.get("f2w_b_trigger_score_ratio"),
            "pd_zone": r.get("pd_zone"),
            "direction": r.get("direction_engine"),
            "regime": r.get("regime_router"),
            "shadow_grade": r.get("shadow_grade"),
            "shadow_score": r.get("shadow_score"),
            "family_grade": r.get("family_grade"),
            "family_score": r.get("family_score"),
            "klines_count": len(klines),
            "source_candle": ts_row.get("candle"),
            "source_ts": ts_row.get("ts") or ts_row.get("generated_at"),
            "outcome_1c": calc_outcome(side, klines, 1),
            "outcome_3c": calc_outcome(side, klines, 3),
            "outcome_6c": calc_outcome(side, klines, 6),
        }
        results.append(item)

    payload = {
        "event": "F2X_B_SHADOW_OUTCOME_TRACKER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "source": str(src),
        "candidate_count": len(candidates),
        "result_count": len(results),
        "error_count": len(errors),
        "aggregate_1c": aggregate(results, "outcome_1c"),
        "aggregate_3c": aggregate(results, "outcome_3c"),
        "aggregate_6c": aggregate(results, "outcome_6c"),
        "results": results,
        "errors": errors,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2X_SHADOW_OUTCOME_TRACKER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"candidate_count={len(candidates)}")
    lines.append(f"result_count={len(results)}")
    lines.append(f"error_count={len(errors)}")

    for label in ["aggregate_1c", "aggregate_3c", "aggregate_6c"]:
        lines.append("")
        lines.append(label.upper())
        for k, v in payload[label].items():
            lines.append(f"{k}={v}")

    lines.append("")
    lines.append("CANDIDATE_OUTCOMES")
    for x in results:
        o1 = x["outcome_1c"]
        o3 = x["outcome_3c"]
        o6 = x["outcome_6c"]
        lines.append(
            "|".join([
                x["pair"],
                x["side"],
                f"ts={x['candidate_ts']}",
                f"ts_source={x['timestamp_source']}",
                f"zone={x['pd_zone']}",
                f"direction={x['direction']}",
                f"regime={x['regime']}",
                f"trigger_ratio={x['trigger_ratio']}",
                f"1c_mfe={o1.get('mfe_pct')}",
                f"1c_mae={o1.get('mae_pct')}",
                f"1c_close={o1.get('close_return_pct')}",
                f"3c_mfe={o3.get('mfe_pct')}",
                f"3c_mae={o3.get('mae_pct')}",
                f"3c_close={o3.get('close_return_pct')}",
                f"6c_mfe={o6.get('mfe_pct')}",
                f"6c_mae={o6.get('mae_pct')}",
                f"6c_close={o6.get('close_return_pct')}",
            ])
        )

    lines.append("")
    lines.append("ERRORS")
    for e in errors:
        lines.append(json.dumps(e, ensure_ascii=False))

    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_json}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")
    lines.append("")
    lines.append("DECISION_HINT")
    lines.append("If result_count > 0 and 3c/6c MFE positive with controlled MAE, trigger model has edge candidate.")
    lines.append("If result_count remains 0, inspect source JSONL timestamp fields.")
    lines.append("No entry/gate/risk behavior changed.")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
