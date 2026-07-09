"""Nexus bot runner — drop-in replacement for clean_core/run_*.sh.

Loads config from config.yaml, patches fetch_recent to use Nexus API,
then runs the same engine loop.

Usage:
    python run_bot.py --bot m30_imbalance
    python run_bot.py --bot h1_imbalance --dry  # dry run, no orders
"""

import json
import os
import sys

import yaml

# --- 1. Patch fetch_recent BEFORE importing engine ---
# Replace backtest.data.fetch_recent with nexus_data.fetch_recent
import backtest.data
import bots.nexus_data as nexus_data
backtest.data.fetch_recent = nexus_data.fetch_recent

# --- 2. Load config ---
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

# --- 3. Parse bot name from env ---
BOT_NAME = os.environ.get("BOT_NAME", "m30_imbalance")
cfg = config.get(BOT_NAME, {})
if not cfg:
    print(f"ERROR: unknown bot '{BOT_NAME}'. Available: {list(config.keys())}")
    sys.exit(1)

MIN_UNIVERSE = int(os.environ.get("MIN_UNIVERSE", "10"))


def _tg_alert(text: str) -> None:
    """Best-effort Telegram alert (no dependency on engine import order)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAMBOTTOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": int(chat_id), "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _normalize_symbol(sym: str) -> str:
    """Freqtrade 'BTC/USDT:USDT' -> 'BTCUSDT'; passthrough for plain symbols."""
    sym = sym.strip()
    if not sym:
        return ""
    if ":" in sym:
        sym = sym.split(":", 1)[0]
    sym = sym.replace("/", "")
    return sym.upper()


def _load_universe_from_json(path: str) -> list[str]:
    """Load symbols from a Revo/Freqtrade pairlist JSON.

    Supports:
      {"pairs": ["BTC/USDT:USDT", ...]}                       (freqtrade_pairlist.json)
      {"pairs": [{"pair": ..., "symbol": "BTCUSDT", ...}]}    (pair_universe_all.json)
    """
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"[nexus-runner] WARNING: cannot read universe source {path}: {exc}")
        return []
    pairs = data.get("pairs", data if isinstance(data, list) else [])
    out: list[str] = []
    for p in pairs:
        if isinstance(p, dict):
            sym = p.get("symbol") or _normalize_symbol(str(p.get("pair", "")))
        else:
            sym = _normalize_symbol(str(p))
        if sym and sym not in out:
            out.append(sym)
    return out


def load_universe(symbols_file: str) -> list[str]:
    """Resolve the trading universe with explicit fallbacks (never silently tiny).

    Order: bots/<symbols_file> -> $UNIVERSE_SOURCE -> runtime/revo/freqtrade_pairlist.json
    -> runtime/revo/pair_universe_all.json -> repo-root universe.txt
    """
    here = os.path.dirname(__file__)
    repo_root = os.path.dirname(here)

    symbols: list[str] = []
    path = os.path.join(here, symbols_file)
    if os.path.exists(path):
        with open(path) as fh:
            symbols = [_normalize_symbol(s) for s in fh.read().split() if s.strip()]
    if symbols:
        print(f"[nexus-runner] Universe from {symbols_file}: {len(symbols)} symbols")
        return symbols

    candidates = []
    env_src = os.environ.get("UNIVERSE_SOURCE")
    if env_src:
        candidates.append(env_src)
    candidates += [
        os.path.join(repo_root, "runtime", "revo", "freqtrade_pairlist.json"),
        os.path.join(repo_root, "runtime", "revo", "pair_universe_all.json"),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            symbols = _load_universe_from_json(cand)
            if symbols:
                print(f"[nexus-runner] Universe from {cand}: {len(symbols)} symbols")
                return symbols

    root_txt = os.path.join(repo_root, "universe.txt")
    if os.path.exists(root_txt):
        with open(root_txt) as fh:
            symbols = [_normalize_symbol(s) for s in fh.read().split() if s.strip()]
        if symbols:
            print(f"[nexus-runner] Universe from {root_txt}: {len(symbols)} symbols")
            return symbols

    return []


# --- 4. Build CLI args from config ---
cli_args = [a for a in sys.argv[1:] if a != "--dry"]  # engine is dry by default; no --dry arg exists
cli_has_symbols = "--symbols" in cli_args
args = []

symbols_from_file = []
if "symbols_file" in cfg and not cli_has_symbols:
    symbols_from_file = load_universe(cfg["symbols_file"])
    if len(symbols_from_file) < MIN_UNIVERSE:
        msg = (
            f"UNIVERSE EMPTY/TOO SMALL (n={len(symbols_from_file)}) for bot {BOT_NAME} — "
            f"check bots/{cfg['symbols_file']}, UNIVERSE_SOURCE, or the pairlist scanner. "
            f"Refusing to silently fall back to the 6-symbol engine default."
        )
        print(f"[nexus-runner] WARNING: {msg}")
        _tg_alert(f"⚠️ {msg}")

for key, val in cfg.items():
    if key == "symbols_file":
        continue
    if isinstance(val, bool):
        if val:
            args.append(f"--{key.replace('_', '-')}")
    else:
        args.extend([f"--{key.replace('_', '-')}", str(val)])

if symbols_from_file:
    args.extend(["--symbols"] + symbols_from_file)

args.extend(cli_args)  # CLI overrides/extra flags go last
args = [str(a) for a in args]

print(f"[nexus-runner] Bot: {BOT_NAME}")
print(f"[nexus-runner] Universe size: {len(symbols_from_file)}")
print(f"[nexus-runner] Args: {args[:20]}{' ...' if len(args) > 20 else ''}")

# --- 5. Import engine and run ---
# Engine uses argparse — pass our built args
sys.argv = ["engine.py"] + args

from clean_core.engine import main
main()
