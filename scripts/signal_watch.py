#!/usr/bin/env python3
"""
signal_watch.py — live watcher for the Signal Copy bot.

Tails the nexus_signal_copy container logs and prints a clean, consolidated
block for every ACTIONABLE signal: the parsed call, its verdict + score, and
(crucially) the ENTRY ROUTING decision produced by the validation fix —
whether the signal is taken at market, held as a pending-limit, or routed to
wait for a pullback because price drifted off the entry zone.

Noise (promo spam, "targets achieved", VIP ads) is dropped: those parse as
UNKNOWN / no_actionable_cue and are ignored unless --verbose.

Usage:
  python scripts/signal_watch.py                 # follow live, all actionable
  python scripts/signal_watch.py --once          # report first actionable, exit
  python scripts/signal_watch.py --since 30m     # replay last 30m then follow
  python scripts/signal_watch.py --verbose       # also show REJECT + noise
  python scripts/signal_watch.py --no-follow      # just scan existing logs, exit

Stdlib only. Shells out to `docker logs`.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

CONTAINER = "nexus_signal_copy"

# [SIGNAL_COPY] parsed: BNBUSDT SHORT | entry 580.31-597.719 | TP [...] | SL 616.869 | lev 10x | src FusionXomegabot
RE_PARSED = re.compile(
    r"\[SIGNAL_COPY\] parsed:\s*(?P<sym>\S+)\s+(?P<side>LONG|SHORT)\s*\|\s*"
    r"entry\s+(?P<entry>[\d.\-]+)\s*\|\s*TP\s+(?P<tp>\[[^\]]*\])\s*\|\s*"
    r"SL\s+(?P<sl>\S+)\s*\|\s*lev\s+(?P<lev>\S+)\s*\|\s*src\s+(?P<src>.+)$"
)
# [SIGNAL_COPY] SYMBOL -> VALID score=84.5 (tp=... sl=... entry=market tf=-)
RE_VERDICT = re.compile(
    r"\[SIGNAL_COPY\]\s+(?P<sym>\S+)\s*->\s*(?P<verdict>VALID|WEAK|REJECT)\s+"
    r"score=(?P<score>[\d.]+)\s*\((?P<meta>[^)]*)\)"
)
# Routing / pending-limit lines from the orchestrator + validation factor detail
RE_PENDING = re.compile(r"Limit setup\s+(?P<sym>\S+)|pending_limit|menunggu harga")
RE_ROUTING = re.compile(r"(better entry — limit|route to pullback/limit|near zone|inside zone)")
RE_MTF = re.compile(r"\[MTF\]\s+(?P<sym>\S+).*?mtf_score=(?P<sc>[\d.]+).*?4h=(?P<h4>\S+)\s+d1=(?P<d1>\S+)")
RE_TV = re.compile(r"\[TV\]\s+(?P<sym>\S+).*?tv_score=(?P<sc>[\d.]+)")
RE_ADV = re.compile(r"\[ADVERSARIAL\]\s+(?P<rest>.+)")

VERDICT_BADGE = {"VALID": "🟢 VALID", "WEAK": "🟡 WEAK", "REJECT": "🔴 REJECT"}


def build_cmd(container: str, since: str | None, follow: bool) -> list[str]:
    cmd = ["docker", "logs"]
    if follow:
        cmd.append("-f")
    if since:
        cmd += ["--since", since]
    elif not follow:
        cmd += ["--tail", "2000"]
    cmd.append(container)
    return cmd


def emit(sig: dict) -> None:
    """Print one consolidated actionable-signal block."""
    v = sig.get("verdict", "?")
    badge = VERDICT_BADGE.get(v, v)
    print("\n" + "═" * 60)
    print(f"  {badge}   {sig.get('sym','?')} {sig.get('side','')}   score={sig.get('score','?')}")
    print("═" * 60)
    if sig.get("entry"):
        print(f"  Entry zone : {sig['entry']}   SL {sig.get('sl','-')}   lev {sig.get('lev','-')}")
    if sig.get("tp"):
        print(f"  Targets    : {sig['tp']}")
    if sig.get("mtf"):
        print(f"  MTF        : score {sig['mtf'][0]}  4h={sig['mtf'][1]} d1={sig['mtf'][2]}")
    if sig.get("tv"):
        print(f"  TradingView: score {sig['tv']}")
    if sig.get("meta"):
        print(f"  Meta       : {sig['meta']}")
    if sig.get("routing"):
        print(f"  ROUTING    : {sig['routing']}")
    if sig.get("adv"):
        print(f"  Adversarial: {sig['adv']}")
    if sig.get("pending"):
        print("  ⏳ PENDING-LIMIT — waiting for price to reach entry (pullback)")
    print("═" * 60, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live watcher for the Signal Copy bot")
    ap.add_argument("--container", default=CONTAINER, help="container name")
    ap.add_argument("--since", default=None, help="replay window, e.g. 30m, 2h")
    ap.add_argument("--once", action="store_true", help="report first actionable signal then exit")
    ap.add_argument("--verbose", action="store_true", help="also show REJECT + noise")
    ap.add_argument("--no-follow", dest="follow", action="store_false", help="scan existing logs then exit")
    args = ap.parse_args()

    cmd = build_cmd(args.container, args.since, args.follow)
    print(f"[watch] {' '.join(cmd)}", file=sys.stderr, flush=True)

    # per-symbol accumulator so we can join parsed+mtf+tv+verdict into one block
    pending: dict[str, dict] = {}

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except FileNotFoundError:
        print("docker not found on PATH", file=sys.stderr)
        return 2
    assert proc.stdout is not None

    noise = 0
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")

            m = RE_PARSED.search(line)
            if m:
                sym = m.group("sym")
                pending[sym] = {
                    "sym": sym, "side": m.group("side"), "entry": m.group("entry"),
                    "tp": m.group("tp"), "sl": m.group("sl"), "lev": m.group("lev"),
                    "src": m.group("src").strip(),
                }
                continue

            m = RE_MTF.search(line)
            if m and m.group("sym") in pending:
                pending[m.group("sym")]["mtf"] = (m.group("sc"), m.group("h4"), m.group("d1"))
                continue

            m = RE_TV.search(line)
            if m and m.group("sym") in pending:
                pending[m.group("sym")]["tv"] = m.group("sc")
                continue

            m = RE_ROUTING.search(line)
            if m:
                # attach routing detail to the most recent parsed symbol without one
                for s in reversed(list(pending)):
                    if "routing" not in pending[s]:
                        pending[s]["routing"] = m.group(1)
                        break
                continue

            m = RE_ADV.search(line)
            if m:
                for s in reversed(list(pending)):
                    if "adv" not in pending[s]:
                        pending[s]["adv"] = m.group("rest").strip()
                        break
                continue

            if RE_PENDING.search(line):
                for s in reversed(list(pending)):
                    pending[s]["pending"] = True
                    break
                continue

            m = RE_VERDICT.search(line)
            if m:
                sym = m.group("sym")
                verdict = m.group("verdict")
                sig = pending.pop(sym, {"sym": sym})
                sig["verdict"] = verdict
                sig["score"] = m.group("score")
                sig["meta"] = m.group("meta")
                if verdict == "REJECT" and not args.verbose:
                    continue
                emit(sig)
                if args.once and verdict in ("VALID", "WEAK"):
                    proc.terminate()
                    return 0
                continue

            if args.verbose and "no_actionable_cue" in line:
                noise += 1
                print(f"  · noise dropped ({noise}): {line[-80:]}", file=sys.stderr, flush=True)

    except KeyboardInterrupt:
        print("\n[watch] stopped", file=sys.stderr)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
