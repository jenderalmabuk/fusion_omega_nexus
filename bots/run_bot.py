"""Nexus bot runner — drop-in replacement for clean_core/run_*.sh.

Loads config from config.yaml, patches fetch_recent to use Nexus API,
then runs the same engine loop.

Usage:
    python run_bot.py --bot m30_imbalance
    python run_bot.py --bot h1_imbalance --dry  # dry run, no orders
"""

import importlib
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

# --- 4. Build CLI args from config ---
cli_args = [a for a in sys.argv[1:] if a != "--dry"]  # engine is dry by default; no --dry arg exists
cli_has_symbols = "--symbols" in cli_args
args = []

def _tg_alert(text: str) -> None:
    """Best-effort Telegram alert (used before engine import)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": int(chat_id), "text": text}, timeout=10)
    except Exception:
        pass


def _normalize_pair(pair: str) -> str:
    """Freqtrade format 'BTC/USDT:USDT' -> 'BTCUSDT'."""
    return pair.split(":")[0].replace("/", "").strip().upper()


def _load_universe_fallback() -> list:
    """Load universe from scanner output when symbols_file is empty."""
    candidates = []
    env_src = os.environ.get("UNIVERSE_SOURCE", "")
    if env_src:
        candidates.append(env_src)
    revo_dir = os.environ.get("REVO_RUNTIME_DIR",
                              os.path.join(os.path.dirname(__file__), "..", "runtime", "revo"))
    candidates.append(os.path.join(revo_dir, "freqtrade_pairlist.json"))
    candidates.append(os.path.join(revo_dir, "pair_universe_all.json"))
    for path in candidates:
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            continue
        raw = data.get("pairs", data) if isinstance(data, dict) else data
        if not isinstance(raw, list):
            continue
        symbols = [_normalize_pair(p) for p in raw if isinstance(p, str) and p.strip()]
        symbols = [s for s in symbols if s.endswith("USDT")]
        if symbols:
            print(f"[nexus-runner] Universe loaded from fallback: {path} ({len(symbols)} symbols)")
            return symbols
    return []


symbols_from_file = []
if "symbols_file" in cfg and not cli_has_symbols:
    try:
        with open(os.path.join(os.path.dirname(__file__), cfg["symbols_file"])) as f:
            symbols_from_file = f.read().split()
    except FileNotFoundError:
        print(f"[nexus-runner] WARNING: symbols_file '{cfg['symbols_file']}' not found")

if not symbols_from_file and not cli_has_symbols:
    symbols_from_file = _load_universe_fallback()

if not cli_has_symbols and len(symbols_from_file) < 10:
    msg = (f"UNIVERSE EMPTY/TOO SMALL (n={len(symbols_from_file)}) for bot '{BOT_NAME}' — "
           f"check bots/universe.txt or UNIVERSE_SOURCE / runtime/revo/freqtrade_pairlist.json")
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
print(f"[nexus-runner] Args: {args}")

# --- 5. Import engine and run ---
# Engine uses argparse — pass our built args
sys.argv = ["engine.py"] + args

from clean_core.engine import main
main()
