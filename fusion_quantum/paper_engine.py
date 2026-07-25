"""Fusion_Quantum paper adapter. Separate from four active engines.

Consumes already-confirmed H4+15m setups. Sends DRY_RUN intents through gateway.
Replace GatewayClient dry_run=False only after explicit live promotion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from gateway.client import GatewayClient
from gateway.order_intent import OrderIntent

SOURCE = "TEST"
AUDIT_PATH = Path(os.getenv("FQ_PAPER_AUDIT", "runtime/fusion_quantum/paper_audit.jsonl"))
RISK_PCT = float(os.getenv("FQ_PAPER_RISK_PCT", "0.0025"))


def intent_from_setup(setup: dict[str, Any]) -> OrderIntent:
    side = str(setup["side"]).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    return OrderIntent(
        source=SOURCE,
        symbol=str(setup["symbol"]).upper(),
        side=side,
        entry_price=float(setup["entry_price"]),
        sl_price=float(setup["sl_price"]),
        tps=[float(setup["tp_price"])],
        risk_pct=RISK_PCT,
        regime="UNKNOWN",
        confidence=float(setup.get("confidence", 0.5)),
        tier="Probe",
        tag="fusion_quantum_h4_15m",
        adv_snapshot={"timeframe": "4h+15m", "paper": True},
    )


async def submit_paper(setup: dict[str, Any], *, client: Any = None) -> dict[str, Any]:
    """Validate first, then open only in dedicated PaperMainnetTrader gateway."""
    intent = intent_from_setup(setup)
    gateway = client or GatewayClient()
    validation = await gateway.execute(intent, dry_run=True)
    accepted = bool(validation.get("ok") and str(validation.get("reason", "")).upper().startswith("DRY_RUN"))
    if accepted:
        execution = await gateway.execute(intent, dry_run=False)
        result = {
            "ok": bool(execution.get("ok")),
            "status": "paper_opened" if execution.get("ok") else "paper_rejected",
            "would_deploy": True,
            "validation": validation,
            "execution": execution,
        }
    else:
        result = {"ok": False, "status": "validation_rejected", "would_deploy": False, "validation": validation}
    record = {"intent": intent.to_dict(), "result": result}
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return result


if __name__ == "__main__":
    raise SystemExit("paper_engine is adapter-only; provide confirmed setup to submit_paper()")
