#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

OUT_PREFIX = "F4X_AL2_PAPER_BRIDGE_CONFIRM_TRADE_ENTRY_STRICT_BYPASS_DRYRUN_ONLY"
STRATEGY = Path("/home/fusion_omega/revo_adaptive/user_data/strategies/RevoAlphaStrategy.py")
RUNTIME = Path("/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")

MARKER_START = "        # CONTROL_TOWER_F4X_AL2_PAPER_BRIDGE_CONFIRM_BYPASS_START\n"
MARKER_END = "        # CONTROL_TOWER_F4X_AL2_PAPER_BRIDGE_CONFIRM_BYPASS_END\n"

PATCH_BLOCK = r'''        # CONTROL_TOWER_F4X_AL2_PAPER_BRIDGE_CONFIRM_BYPASS_START
        # Strict dry-run paper-bridge bypass for scanner-driven AK2 K-intent only.
        # This does NOT unlock live, risk-up, gate-loosen, or generic WATCH/RECHECK/DENY entry.
        try:
            import json as _f4x_al2_json
            import os as _f4x_al2_os
            from pathlib import Path as _f4x_al2_Path
            from datetime import datetime as _f4x_al2_datetime, timezone as _f4x_al2_timezone

            def _f4x_al2_truthy(_v):
                return str(_v).strip().lower() in {"1", "true", "yes", "y", "on"}

            def _f4x_al2_side(_v):
                _s = str(_v or "").strip().upper()
                if _s in {"LONG", "BUY", "LONG_ONLY"}:
                    return "LONG"
                if _s in {"SHORT", "SELL", "SHORT_ONLY"}:
                    return "SHORT"
                return _s

            def _f4x_al2_runtime_dir():
                _env = str(_f4x_al2_os.getenv("REVO_RUNTIME_DIR", "")).strip()
                _candidates = []
                if _env:
                    _candidates.append(_f4x_al2_Path(_env))
                _candidates.extend([
                    _f4x_al2_Path("/freqtrade/user_data/revo_alpha/runtime/bybit"),
                    _f4x_al2_Path("/freqtrade/user_data/revo_alpha/runtime"),
                    _f4x_al2_Path("/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"),
                    _f4x_al2_Path("user_data/revo_alpha/runtime/bybit"),
                ])
                for _p in _candidates:
                    try:
                        if _p.exists():
                            return _p
                    except Exception:
                        continue
                return _candidates[0]

            def _f4x_al2_age_ok(_generated_at, _max_age_sec):
                try:
                    _raw = str(_generated_at or "").strip().replace("Z", "+00:00")
                    if not _raw:
                        return False
                    _dt = _f4x_al2_datetime.fromisoformat(_raw)
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=_f4x_al2_timezone.utc)
                    _age = (_f4x_al2_datetime.now(_f4x_al2_timezone.utc) - _dt).total_seconds()
                    return 0 <= _age <= float(_max_age_sec)
                except Exception:
                    return False

            _f4x_al2_enabled = _f4x_al2_truthy(_f4x_al2_os.getenv("F4X_AL2_PAPER_BRIDGE_CONFIRM_BYPASS", "true"))
            _f4x_al2_bypass = False
            _f4x_al2_reason = "NOT_EVALUATED"
            _f4x_al2_payload = {}

            if _f4x_al2_enabled and mode == "PAPER":
                _runtime = _f4x_al2_runtime_dir()
                _active_path = _runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
                if _active_path.exists():
                    try:
                        _active = _f4x_al2_json.loads(_active_path.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
                        _active = {}

                    _max_age = float(_f4x_al2_os.getenv("F4X_AL2_MAX_INTENT_AGE_SEC", "1800") or "1800")
                    _active_age_ok = _f4x_al2_age_ok(_active.get("generated_at"), _max_age)

                    _top_guard_ok = (
                        bool(_active.get("has_order_intent")) is True and
                        str(_active.get("paper_order_mode", "")).upper() == "STRICT_ALLOW_ONLY" and
                        bool(_active.get("paper_order_allowed")) is True and
                        bool(_active.get("dry_run_only")) is True and
                        bool(_active.get("live_allowed")) is False and
                        bool(_active.get("risk_up_allowed")) is False and
                        bool(_active.get("gate_loosen_allowed")) is False and
                        bool(_active.get("entry_from_watch_recheck_deny_allowed")) is False and
                        _active_age_ok
                    )

                    _intents = _active.get("order_intents") or []
                    if not isinstance(_intents, list):
                        _intents = []

                    for _intent in _intents:
                        if not isinstance(_intent, dict):
                            continue

                        _intent_pair = str(_intent.get("order_pair") or _intent.get("pair") or "").strip()
                        _intent_side = _f4x_al2_side(_intent.get("order_side") or _intent.get("side") or _intent.get("direction"))
                        _source_text = (
                            str(_intent.get("intent_source", "")) + "|" +
                            str(_active.get("mode", "")) + "|" +
                            str(_intent.get("scanner_selection_source", "")) + "|" +
                            str(_intent.get("canary_reason", ""))
                        ).upper()

                        _intent_guard_ok = (
                            _top_guard_ok and
                            _intent_pair == str(pair).strip() and
                            _intent_side == side_u and
                            bool(_intent.get("allow_paper_entry")) is True and
                            bool(_intent.get("would_order")) is True and
                            bool(_intent.get("dry_run_only")) is True and
                            bool(_intent.get("live_allowed")) is False and
                            bool(_intent.get("risk_up_allowed")) is False and
                            bool(_intent.get("gate_loosen_allowed")) is False and
                            bool(_intent.get("entry_from_watch_recheck_deny_allowed")) is False and
                            str(_intent.get("intent_state", "")).upper() == "ALLOW_PAPER_ENTRY" and
                            str(_intent.get("paper_action", "")).upper() == "ALLOW_PAPER_ENTRY" and
                            ("F4X_AK2" in _source_text or "SCANNER_DRIVEN_STRICT" in _source_text)
                        )

                        if _intent_guard_ok:
                            _f4x_al2_bypass = True
                            _f4x_al2_reason = "STRICT_SCANNER_DRIVEN_K_PAPER_INTENT_BYPASS_CONFIRMED"
                            _f4x_al2_payload = {
                                "event": "F4X_AL2_CONFIRM_TRADE_ENTRY_BYPASS_ALLOWED",
                                "pair": pair,
                                "side": side_u,
                                "mode": mode,
                                "entry_tag": entry_tag,
                                "rate": float(rate or 0.0),
                                "amount": float(amount or 0.0),
                                "paper_bridge_active_path": str(_active_path),
                                "intent_source": str(_intent.get("intent_source", "")),
                                "intent_state": str(_intent.get("intent_state", "")),
                                "paper_action": str(_intent.get("paper_action", "")),
                                "score": float(_intent.get("score") or 0.0),
                                "cvdoi": str(_intent.get("cvdoi", "")),
                                "trigger": str(_intent.get("trigger", "")),
                                "smc": str(_intent.get("smc", "")),
                                "previous_allowed_from_dataframe": bool(allowed),
                                "previous_reason": str(last.get(reason_col, last.get(fallback_reason_col, "UNKNOWN"))),
                                "reason": _f4x_al2_reason,
                            }
                            break
                else:
                    _f4x_al2_reason = "ACTIVE_SIGNAL_FILE_MISSING"

            if _f4x_al2_bypass:
                try:
                    write_runtime_event("F4X_AL2_CONFIRM_TRADE_ENTRY_BYPASS_EVENTS.jsonl", _f4x_al2_payload)
                except Exception:
                    pass
                try:
                    write_shadow_event(_f4x_al2_payload)
                except Exception:
                    pass
                return True
        except Exception as _f4x_al2_exc:
            try:
                write_runtime_event("F4X_AL2_CONFIRM_TRADE_ENTRY_BYPASS_EVENTS.jsonl", {
                    "event": "F4X_AL2_CONFIRM_TRADE_ENTRY_BYPASS_ERROR_HOLD",
                    "pair": pair,
                    "side": side_u,
                    "mode": mode,
                    "error": str(_f4x_al2_exc)[:240],
                })
            except Exception:
                pass
        # CONTROL_TOWER_F4X_AL2_PAPER_BRIDGE_CONFIRM_BYPASS_END
'''

def main() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not STRATEGY.exists():
        raise SystemExit(f"strategy not found: {STRATEGY}")

    txt = STRATEGY.read_text(encoding="utf-8", errors="replace")
    generated_at = datetime.now(timezone.utc).isoformat()
    backup = STRATEGY.with_suffix(STRATEGY.suffix + f".F4X_AL2_BACKUP_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")

    if MARKER_START in txt:
        final_decision = "F4X_AL2_ALREADY_INSTALLED"
        patched = False
        backup_path = None
    else:
        anchor = '        mode = str(REVO_GATE_MODE or "PAPER").upper()\n        if not allowed:\n'
        if anchor not in txt:
            raise SystemExit("ANCHOR_NOT_FOUND: mode line followed by if not allowed")
        shutil.copy2(STRATEGY, backup)
        txt = txt.replace(
            anchor,
            '        mode = str(REVO_GATE_MODE or "PAPER").upper()\n' + PATCH_BLOCK + '        if not allowed:\n',
            1,
        )
        STRATEGY.write_text(txt, encoding="utf-8")
        final_decision = "F4X_AL2_STRICT_PAPER_BRIDGE_CONFIRM_BYPASS_PATCHED"
        patched = True
        backup_path = str(backup)

    result = {
        "generated_at": generated_at,
        "mode": "PAPER_BRIDGE_CONFIRM_TRADE_ENTRY_STRICT_BYPASS_DRYRUN_ONLY_PATCH",
        "final_decision": final_decision,
        "patched": patched,
        "strategy": str(STRATEGY),
        "backup_path": backup_path,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "policy": [
            "bypass hanya untuk F4X_AK2 / SCANNER_DRIVEN_STRICT K intent",
            "dry_run_only harus true",
            "paper_order_mode harus STRICT_ALLOW_ONLY",
            "pair dan side harus sama",
            "live/risk_up/gate_loosen/watch_recheck_deny harus false",
            "intent max age default 1800 sec",
        ],
    }

    full = RUNTIME / f"{OUT_PREFIX}_FULL.json"
    active = RUNTIME / f"{OUT_PREFIX}_ACTIVE.json"
    compact = RUNTIME / f"{OUT_PREFIX}_COMPACT.txt"
    full.write_text(json.dumps(result, indent=2), encoding="utf-8")
    active.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines = [
        "F4X_AL2_PAPER_BRIDGE_CONFIRM_TRADE_ENTRY_STRICT_BYPASS_DRYRUN_ONLY_COMPACT",
        f"generated_at={generated_at}",
        "mode=PAPER_BRIDGE_CONFIRM_TRADE_ENTRY_STRICT_BYPASS_DRYRUN_ONLY_PATCH",
        f"final_decision={final_decision}",
        f"patched={patched}",
        f"strategy={STRATEGY}",
        f"backup_path={backup_path}",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "entry_from_watch_recheck_deny=HOLD",
        "DECISION_POLICY",
        "Patch only. No K write. No order execution.",
        "Bypass only for scanner-driven strict AK2 paper intent.",
        "No global confirm_trade_entry unlock.",
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
    ]
    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text())

if __name__ == "__main__":
    main()
