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
import sys
from pathlib import Path

# Add nexus root to path
sys.path.insert(0, str(Path(__file__).parent))

from nexus.data_bridge import get_data_bridge
from signal_copy.orchestrator import SignalCopyOrchestrator
from signal_copy.signal_schema import SignalSource


async def main(dry_run: bool = True):
    print("[NEXUS SIGNAL COPY] Starting...")
    
    # === Data bridge: scanner cache → validator ===
    bridge = get_data_bridge()
    print(f"[NEXUS SIGNAL COPY] Data bridge: {bridge.cache_dir}")
    
    # === Orchestrator ===
    orch = SignalCopyOrchestrator(
        metrics_provider=bridge,
        trader=None,       # TODO: BinanceTestnetTrader when --live
        risk_mgr=None,    # TODO: RiskManager when --live
        confirm_bot=None,  # TODO: TelegramConfirmBot
        notifier=None,     # TODO: telegram notify
        dry_run=dry_run,
        auto_execute=False,
    )
    
    print(f"[NEXUS SIGNAL COPY] Orchestrator ready (dry_run={dry_run})")
    print("[NEXUS SIGNAL COPY] Waiting for Telegram signals...")
    
    # === TODO: wire TelegramSignalListener (Telethon user-account) ===
    # For now: manual test via signal injection in code below
    
    # Keep alive
    while True:
        await asyncio.sleep(60)
