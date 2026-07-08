"""Nexus bot runner — drop-in replacement for clean_core/run_*.sh.

Loads config from config.yaml, patches fetch_recent to use Nexus API,
then runs the same engine loop.

Usage:
    python run_bot.py --bot m30_imbalance
    python run_bot.py --bot h1_imbalance --dry  # dry run, no orders
"""

import importlib
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

symbols_from_file = []
if "symbols_file" in cfg and not cli_has_symbols:
    with open(os.path.join(os.path.dirname(__file__), cfg["symbols_file"])) as f:
        symbols_from_file = f.read().split()

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
