"""Standalone Execution Gateway service.

Run:
    export GATEWAY_TOKEN=$(openssl rand -hex 24)
    export BINANCE_TESTNET_API_KEY=...
    export BINANCE_TESTNET_API_SECRET=...
    export STARTING_BALANCE=1000
    uvicorn gateway.run_gateway:app --host 127.0.0.1 --port 8787

This is the ONLY process that talks to the exchange. All engines
(clean_core M30/H1, signal_copy, and optionally revo) POST OrderIntents to
http://127.0.0.1:8787/gateway/execute.

It reuses the mature components already in the repo:
    - risk.risk_engine.RiskManager        (one portfolio, one set of limits)
    - execution.binance_testnet_trader    (one exchange connection,
                                           partial TP / trailing / journal)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.api import build_router
from gateway.service import ExecutionGateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gateway.main")

_gateway: ExecutionGateway | None = None


def _build_gateway() -> ExecutionGateway:
    """Wire the ONE RiskManager + ONE trader. Adjust imports/ctor args here
    if your trader constructor differs — this is the single place to do it."""
    from risk.risk_engine import RiskManager
    from execution.binance_testnet_trader import BinanceTestnetTrader  # adjust name if different

    starting_balance = float(os.getenv("STARTING_BALANCE", "1000"))
    risk_mgr = RiskManager(starting_balance=starting_balance)

    # Use environment variables for credentials (same as trader expects)
    # Support both BINANCE_TESTNET_API_SECRET (legacy) and BINANCE_TESTNET_SECRET (current .env)
    api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "") or os.getenv("BINANCE_TESTNET_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET must be set")

    trader = BinanceTestnetTrader(api_key=api_key, api_secret=api_secret)  # uses env-based credentials
    # Let RiskManager see the trader's live positions for exposure/cluster math
    if hasattr(risk_mgr, "attach_trader"):
        risk_mgr.attach_trader(trader)
    elif hasattr(risk_mgr, "trader"):
        risk_mgr.trader = trader

    return ExecutionGateway(trader=trader, risk_mgr=risk_mgr)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gateway
    _gateway = _build_gateway()
    # start the trader's order-router / position-manager loops if it has them
    start = getattr(_gateway.trader, "start", None)
    task = None
    if callable(start):
        result = start()
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
    logger.info("[GATEWAY] up — single execution point ready on /gateway")
    yield
    stop = getattr(_gateway.trader, "stop", None)
    if callable(stop):
        result = stop()
        if asyncio.iscoroutine(result):
            await result
    if task:
        task.cancel()


class _GatewayProxy:
    """Routes are registered before startup, but the real gateway is only
    built inside lifespan (so the trader connects on the event loop).
    This proxy forwards every attribute access to the live instance."""

    def __getattr__(self, name):
        if _gateway is None:
            raise RuntimeError("gateway not initialized yet (still starting up)")
        return getattr(_gateway, name)


app = FastAPI(title="Fusion Execution Gateway", version="1.0.0", lifespan=lifespan)
app.include_router(build_router(_GatewayProxy()), prefix="/gateway")
