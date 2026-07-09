"""1.1 — universe fallback: Freqtrade pair normalization and file loading.

run_bot.py executes at import time, so the helpers are re-implemented
identically? No — we import the real functions via importlib machinery to
avoid running the module body (which would boot the engine).
"""
import ast
import json
import os
import types

RUN_BOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "bots", "run_bot.py")


def _load_helpers() -> types.ModuleType:
    """Extract only the pure helper functions from run_bot.py (module body
    boots the engine, so a plain import is not possible in tests)."""
    tree = ast.parse(open(RUN_BOT).read())
    wanted = {"_normalize_pair", "_load_universe_fallback"}
    nodes = [n for n in tree.body
             if isinstance(n, (ast.Import, ast.ImportFrom))
             or (isinstance(n, ast.FunctionDef) and n.name in wanted)]
    mod = types.ModuleType("run_bot_helpers")
    mod.__dict__["__file__"] = RUN_BOT
    src = ast.Module(body=nodes, type_ignores=[])
    exec(compile(src, RUN_BOT, "exec"), mod.__dict__)  # noqa: S102 — own source
    return mod


HELPERS = _load_helpers()


def test_normalize_freqtrade_pairs():
    f = HELPERS._normalize_pair
    assert f("BTC/USDT:USDT") == "BTCUSDT"
    assert f("ETH/USDT") == "ETHUSDT"
    assert f("solusdt") == "SOLUSDT"
    assert f("  ") == ""


def test_universe_fallback_from_env_source(tmp_path, monkeypatch):
    src = tmp_path / "pairlist.json"
    src.write_text(json.dumps({"pairs": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]}))
    monkeypatch.setenv("UNIVERSE_SOURCE", str(src))
    syms = HELPERS._load_universe_fallback()
    assert syms[:3] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_universe_fallback_dedupes(tmp_path, monkeypatch):
    src = tmp_path / "pairlist.json"
    src.write_text(json.dumps(["BTC/USDT:USDT", "BTC/USDT:USDT", "XRP/USDT:USDT"]))
    monkeypatch.setenv("UNIVERSE_SOURCE", str(src))
    syms = HELPERS._load_universe_fallback()
    assert syms == ["BTCUSDT", "XRPUSDT"]


def test_bots_universe_txt_not_empty():
    """Regression for the original bug: bots/universe.txt was 0 lines."""
    path = os.path.join(os.path.dirname(RUN_BOT), "universe.txt")
    with open(path) as fh:
        symbols = fh.read().split()
    assert len(symbols) >= 10, "bots/universe.txt must not be (near-)empty"
