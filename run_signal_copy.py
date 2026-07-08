#!/usr/bin/env python3
"""
Fusion Omega Nexus — Signal Copy Pipeline

Entry point: Telegram signal listener → parser → adversarial → validator → executor.
Data from Nexus scanner cache (drop-in for AdvancedDataEngine).

Usage:
    python run_signal_copy.py            # dry-run (validate + notify only)
    python run_signal_copy.py --live     # live with testnet execution
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add nexus root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env
from dotenv import load_dotenv
load_dotenv()

from nexus.data_bridge import get_data_bridge
from signal_copy.orchestrator import SignalCopyOrchestrator
from signal_copy.signal_schema import SignalSource
from signal_copy.listeners.telegram_listener import TelegramSignalListener


async def main(dry_run: bool = True):
    print("[NEXUS SIGNAL COPY] Starting...")
    
    # === Data bridge: scanner cache → validator ===
    bridge = get_data_bridge()
    print(f"[NEXUS SIGNAL COPY] Data bridge: {bridge.cache_dir}")
    
    # === Build components (trader, risk_mgr, notifier, confirm_bot) ===
    trader = None
    risk_mgr = None
    notifier = None
    confirm_bot = None
    
    # Telegram Confirm Bot
    try:
        from signal_copy.telegram_confirm_bot import TelegramConfirmBot
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = int(os.getenv("TELEGRAM_CHAT_ID", 0))
        if bot_token and chat_id:
            # Create with empty deps — orchestrator will inject confirmations + decision callback
            confirm_bot = TelegramConfirmBot(
                bot_token=bot_token,
                chat_id=chat_id,
                confirmations=None,   # injected by orchestrator
                on_decision=None,     # injected by orchestrator
            )
            print(f"[NEXUS SIGNAL COPY] TelegramConfirmBot ready (pending wiring)")
        else:
            print(f"[NEXUS SIGNAL COPY] No bot token/chat_id — confirm bot disabled")
    except Exception as exc:
        print(f"[NEXUS SIGNAL COPY] Confirm bot unavailable: {exc}")
    
    if not dry_run:
        # Risk Manager (Kelly sizing)
        try:
            from risk.risk_engine import RiskManager
            risk_mgr = RiskManager(starting_balance=750.0)
            print(f"[NEXUS SIGNAL COPY] RiskManager ready (start=750.0)")
        except Exception as exc:
            print(f"[NEXUS SIGNAL COPY] RiskManager unavailable: {exc}")
        
        # Binance Testnet Trader
        try:
            from execution.binance_testnet_trader import BinanceTestnetTrader
            api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
            api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
            if api_key and api_secret:
                trader = BinanceTestnetTrader(
                    api_key=api_key,
                    api_secret=api_secret,
                    leverage=10,
                )
                print(f"[NEXUS SIGNAL COPY] BinanceTestnetTrader ready")
            else:
                print(f"[NEXUS SIGNAL COPY] No Binance testnet credentials — trader disabled")
        except Exception as exc:
            print(f"[NEXUS SIGNAL COPY] Trader unavailable: {exc}")
    
    # === Orchestrator ===
    orch = SignalCopyOrchestrator(
        metrics_provider=bridge,
        trader=trader,
        risk_mgr=risk_mgr,
        confirm_bot=None,  # Wired below after construction
        notifier=notifier,
        dry_run=dry_run,
        auto_execute=False,
    )
    
    # Wire confirm bot to orchestrator's internal confirmation manager
    if confirm_bot is not None:
        try:
            # Inject deps on bot instance directly
            confirm_bot.confirmations = orch.confirmations
            confirm_bot.on_decision = orch._execute_token
            # Set confirm_bot on orchestrator
            orch.confirm_bot = confirm_bot
            print(f"[NEXUS SIGNAL COPY] Confirm bot wired to orchestrator")
        except Exception as exc:
            print(f"[NEXUS SIGNAL COPY] Confirm bot wiring failed: {exc}")
    
    print(f"[NEXUS SIGNAL COPY] Orchestrator ready (dry_run={dry_run})")
    
    # === Telegram Listener (Telethon user-account) ===
    api_id = int(os.getenv("TELEGRAM_API_ID", 0))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    channels_env = os.getenv("SIGNAL_COPY_TG_CHANNELS", "").strip()
    channels = [int(c.strip()) for c in channels_env.split(",") if c.strip()] if channels_env else None
    
    if not api_id or not api_hash:
        print("[NEXUS SIGNAL COPY] WARNING: TELEGRAM_API_ID/HASH not set. Listener disabled.")
        print("[NEXUS SIGNAL COPY] Waiting for manual signal injection...")
        while True:
            await asyncio.sleep(60)
    
    listener = TelegramSignalListener(
        on_message=lambda text, name, chat_id, image=None: orch.handle_incoming_text(
            text, source_name=name, source_chat_id=chat_id, image=image
        ),
        api_id=api_id,
        api_hash=api_hash,
        session_name="signal_copy_session",
        channels=channels,
    )
    
    print(f"[NEXUS SIGNAL COPY] Starting Telegram listener (channels={channels or 'ALL joined'})...")
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        print("\n[NEXUS SIGNAL COPY] Shutdown requested")
    finally:
        await listener.stop()
        print("[NEXUS SIGNAL COPY] Stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nexus Signal Copy Pipeline")
    parser.add_argument("--live", action="store_true", help="Live mode (testnet execution)")
    args = parser.parse_args()
    
    try:
        asyncio.run(main(dry_run=not args.live))
    except KeyboardInterrupt:
        print("\n[NEXUS SIGNAL COPY] Shutdown")