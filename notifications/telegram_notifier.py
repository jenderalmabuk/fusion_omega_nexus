"""
Minimal stub for notifications module.
Wire later with real Telegram bot integration.
"""

def send_open_trade(data: dict) -> None:
    """Stub: real Telegram notification wired later."""
    import logging
    logging.getLogger("fusion_nexus").info(
        "[NOTIFY] Open trade: %s %s @ %s", 
        data.get("symbol", ""), data.get("side", ""), data.get("entry", 0)
    )