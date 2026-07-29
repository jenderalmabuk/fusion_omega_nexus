"""Daily A/B report: Legacy H1 control (both) vs H1 short candidate.

Compares the candidate against the CONTROL'S SELL SUBSET over the SAME
timestamp window, so regime differences cannot flatter either arm.
Engines stay separate; PnL is never pooled. n<8 slices are flagged UNRELIABLE.
"""
from __future__ import annotations
import json, glob, random, statistics, sys
from datetime import datetime, timezone

random.seed(11)
STATE = "/home/fusion_omega/fusion_omega_nexus/runtime/state"
ARMS = {
    "CONTROL Legacy H1 (both)": f"{STATE}/forward_trades_H1_both_h1_adversarial.jsonl",
    "CANDIDATE H1 short-only": f"{STATE}/forward_trades_H1_short_h1_short_candidate.jsonl",
}
GATE_N, GATE_PF, GATE_PWIN = 30, 1.30, 0.80


def load(path):
    out = []
    for fn in glob.glob(path):
        try:
            fh = open(fn)
        except OSError:
            continue
        for line in fh:
            if '"CLOSE"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("pnl") is None:
                continue
            try:
                d["_t"] = datetime.fromisoformat(str(d["ts"]).replace("Z", "+00:00"))
            except Exception:
                continue
            if d["_t"].tzinfo is None:
                d["_t"] = d["_t"].replace(tzinfo=timezone.utc)
            out.append(d)
    out.sort(key=lambda x: x["_t"])
    return out


def stats(rows):
    if not rows:
        return None
    p = [r["pnl"] for r in rows]
    gp = sum(x for x in p if x > 0)
    gl = abs(sum(x for x in p if x <= 0))
    eq = peak = dd = 0.0
    for x in p:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dict(n=len(p), wr=sum(x > 0 for x in p) / len(p) * 100,
                pf=(gp / gl) if gl else float("inf"), net=sum(p),
                exp=sum(p) / len(p), dd=dd)


def boot_pf(rows, iters=5000):
    if len(rows) < 3:
        return None, None
    p = [r["pnl"] for r in rows]
    wins = 0
    pfs = []
    for _ in range(iters):
        s = [random.choice(p) for _ in p]
        gp = sum(x for x in s if x > 0)
        gl = abs(sum(x for x in s if x <= 0))
        pf = (gp / gl) if gl else 999.0
        pfs.append(pf)
        wins += pf > 1
    pfs.sort()
    return wins / iters, (pfs[int(.025 * iters)], pfs[int(.975 * iters)])


def concentration(rows):
    if not rows:
        return None
    by = {}
    for r in rows:
        by[r.get("symbol", "?")] = by.get(r.get("symbol", "?"), 0) + abs(r["pnl"])
    tot = sum(by.values())
    return (max(by.values()) / tot * 100) if tot else None


def fmt(label, s, extra=""):
    if not s:
        return f"- {label}: no closed trades yet"
    flag = "  UNRELIABLE n<8" if s["n"] < 8 else ""
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"- {label}: n={s['n']} WR={s['wr']:.1f}% PF={pf} "
            f"net={s['net']:+.2f} exp={s['exp']:+.2f} maxDD={s['dd']:.2f}{extra}{flag}")


def main():
    data = {k: load(v) for k, v in ARMS.items()}
    cand = data["CANDIDATE H1 short-only"]
    ctrl = data["CONTROL Legacy H1 (both)"]

    lines = ["## A/B Legacy H1 — control(both) vs candidate(short)", ""]
    for name, rows in data.items():
        lines.append(fmt(name, stats(rows)))

    # Paired window: candidate lifetime only, control restricted to SELL.
    if cand:
        t0 = cand[0]["_t"]
        t1 = cand[-1]["_t"]
        ctrl_sell = [r for r in ctrl if r.get("side") == "SELL" and t0 <= r["_t"] <= t1]
        ctrl_buy = [r for r in ctrl if r.get("side") == "BUY" and t0 <= r["_t"] <= t1]
        lines += ["", f"### Paired window {t0:%Y-%m-%d %H:%M} → {t1:%Y-%m-%d %H:%M} UTC"]
        lines.append(fmt("candidate (short)", stats(cand)))
        lines.append(fmt("control SELL subset", stats(ctrl_sell)))
        lines.append(fmt("control BUY subset", stats(ctrl_buy)))
    else:
        lines += ["", "### Paired window: candidate has no closed trade yet — nothing comparable."]

    # Promotion gate, candidate only.
    s = stats(cand)
    lines += ["", "### Promotion gate (candidate)"]
    if not s:
        lines.append("- [ ] waiting for first closed trade")
    else:
        pw, ci = boot_pf(cand)
        conc = concentration(cand)
        pf_ok = s["pf"] >= GATE_PF
        checks = [
            (f"n>={GATE_N} (now {s['n']})", s["n"] >= GATE_N),
            (f"PF>={GATE_PF} (now {'inf' if s['pf']==float('inf') else round(s['pf'],2)})", pf_ok),
            (f"expectancy>0 (now {s['exp']:+.2f})", s["exp"] > 0),
            (f"P(PF>1)>={GATE_PWIN:.0%} (now {'n/a' if pw is None else f'{pw:.0%}'})",
             bool(pw is not None and pw >= GATE_PWIN)),
            (f"top-symbol share<50% (now {'n/a' if conc is None else f'{conc:.0f}%'})",
             bool(conc is not None and conc < 50)),
        ]
        for label, ok in checks:
            lines.append(f"- [{'x' if ok else ' '}] {label}")
        if ci:
            lines.append(f"- bootstrap PF 95% CI [{ci[0]:.2f}, {ci[1]:.2f}]")
        if s["n"] < 8:
            lines.append("- VERDICT: INCONCLUSIVE (n<8) — do not act on these numbers.")
        elif all(ok for _, ok in checks):
            lines.append("- VERDICT: gate PASSED — eligible for micro-real review.")
        else:
            lines.append("- VERDICT: still paper-only; gate not met.")

    lines += ["", "Paper/dry-run. PnL per engine, never pooled."]
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
