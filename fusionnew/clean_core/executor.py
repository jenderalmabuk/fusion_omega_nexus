"""Thin Binance USDⓈ-M Futures TESTNET executor (signed REST). Clean + self-contained.

DRY mode logs intended orders instead of sending. Real mode places:
  - LIMIT entry (GTC)
  - STOP_MARKET SL (closePosition, reduceOnly)
  - TAKE_PROFIT_MARKET TP (closePosition, reduceOnly)
Credentials reused from config.BINANCE_TESTNET_API_KEY/SECRET (testnet only).
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from typing import Any, Dict, Optional

import requests

from config import BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET

BASE = "https://testnet.binancefuture.com"


class FuturesTestnet:
    def __init__(self, dry: bool = True, api_key: str = "", api_secret: str = ""):
        self.dry = dry
        self.key = api_key or BINANCE_TESTNET_API_KEY or ""
        self.secret = (api_secret or BINANCE_TESTNET_API_SECRET or "").encode()
        self._filters: Dict[str, Dict[str, float]] = {}
        self.s = requests.Session()
        self.s.headers.update({"X-MBX-APIKEY": self.key})

    # ---- signing ----
    def _sign(self, params: Dict[str, Any]) -> str:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        qs = urllib.parse.urlencode(params)
        sig = hmac.new(self.secret, qs.encode(), hashlib.sha256).hexdigest()
        return qs + "&signature=" + sig

    def _signed(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        qs = self._sign(dict(params or {}))
        url = f"{BASE}{path}?{qs}"
        r = self.s.request(method, url, timeout=15)
        r.raise_for_status()
        return r.json()

    def _public(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        r = self.s.get(f"{BASE}{path}", params=params or {}, timeout=15)
        r.raise_for_status()
        return r.json()

    # ---- read ----
    def ping(self) -> bool:
        self._public("/fapi/v1/time")
        return True

    def balance(self, asset: str = "USDT") -> float:
        for b in self._signed("GET", "/fapi/v2/balance"):
            if b.get("asset") == asset:
                return float(b.get("balance", 0.0))
        return 0.0

    def position(self, symbol: str) -> float:
        data = self._signed("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        return float(data[0].get("positionAmt", 0.0)) if data else 0.0

    def open_orders(self, symbol: str) -> list:
        return self._signed("GET", "/fapi/v1/openOrders", {"symbol": symbol})

    # ---- precision ----
    def _f(self, symbol: str) -> Dict[str, float]:
        if symbol not in self._filters:
            info = self._public("/fapi/v1/exchangeInfo")
            for s in info.get("symbols", []):
                step = tick = 0.0
                for fl in s.get("filters", []):
                    if fl["filterType"] == "LOT_SIZE":
                        step = float(fl["stepSize"])
                    elif fl["filterType"] == "PRICE_FILTER":
                        tick = float(fl["tickSize"])
                self._filters[s["symbol"]] = {"step": step, "tick": tick}
        return self._filters.get(symbol, {"step": 0.001, "tick": 0.01})

    @staticmethod
    def _round_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return round(round(value / step) * step, 8)

    def round_qty(self, symbol: str, qty: float) -> float:
        # DRY mode: NEVER call testnet exchangeInfo (many altcoins are missing
        # on testnet; a network error here used to raise and consume the signal
        # before it was ever placed — see engine seen-key handling). Generic
        # 6-decimal rounding is sufficient for paper trades and never raises.
        if self.dry:
            return round(float(qty), 6)
        return self._round_step(qty, self._f(symbol)["step"])

    def round_price(self, symbol: str, price: float) -> float:
        if self.dry:
            return round(float(price), 6)
        return self._round_step(price, self._f(symbol)["tick"])

    # ---- write ----
    def set_leverage(self, symbol: str, lev: int) -> None:
        if self.dry:
            print(f"[DRY] set_leverage {symbol} x{lev}")
            return
        self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": lev})

    def limit_entry(self, symbol: str, side: str, qty: float, price: float) -> Dict[str, Any]:
        p = {"symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
             "quantity": self.round_qty(symbol, qty), "price": self.round_price(symbol, price)}
        if self.dry:
            print(f"[DRY] LIMIT {side} {symbol} qty={p['quantity']} @ {p['price']}")
            return {"dry": True, **p}
        return self._signed("POST", "/fapi/v1/order", p)

    def stop_market(self, symbol: str, side: str, stop_price: float) -> Dict[str, Any]:
        p = {"symbol": symbol, "side": side, "type": "STOP_MARKET",
             "stopPrice": self.round_price(symbol, stop_price), "closePosition": "true"}
        if self.dry:
            print(f"[DRY] STOP_MARKET(SL) {side} {symbol} @ {p['stopPrice']}")
            return {"dry": True, **p}
        return self._signed("POST", "/fapi/v1/order", p)

    def take_profit_market(self, symbol: str, side: str, stop_price: float) -> Dict[str, Any]:
        p = {"symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET",
             "stopPrice": self.round_price(symbol, stop_price), "closePosition": "true"}
        if self.dry:
            print(f"[DRY] TAKE_PROFIT(TP) {side} {symbol} @ {p['stopPrice']}")
            return {"dry": True, **p}
        return self._signed("POST", "/fapi/v1/order", p)

    def cancel_all(self, symbol: str) -> None:
        if self.dry:
            print(f"[DRY] cancel_all {symbol}")
            return
        self._signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})

    def limit_reduce(self, symbol: str, side: str, qty: float, price: float) -> Dict[str, Any]:
        """Reduce-only LIMIT (hardware TP backup — survives bot/server downtime)."""
        p = {"symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
             "quantity": self.round_qty(symbol, qty), "price": self.round_price(symbol, price),
             "reduceOnly": "true"}
        if self.dry:
            print(f"[DRY] LIMIT-REDUCE(TP) {side} {symbol} qty={p['quantity']} @ {p['price']}")
            return {"dry": True, **p}
        return self._signed("POST", "/fapi/v1/order", p)

    def market(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> Dict[str, Any]:
        p = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": self.round_qty(symbol, qty)}
        if reduce_only:
            p["reduceOnly"] = "true"
        if self.dry:
            print(f"[DRY] MARKET {side} {symbol} qty={p['quantity']} reduceOnly={reduce_only}")
            return {"dry": True, **p}
        return self._signed("POST", "/fapi/v1/order", p)

    # ---- Algo Order API: hardware conditional SL/TP (survives bot/server downtime) ----
    def algo_conditional(self, symbol: str, side: str, order_type: str, trigger: float, qty: float) -> Dict[str, Any]:
        """Hardware STOP_MARKET / TAKE_PROFIT_MARKET via /fapi/v1/algoOrder (post-2025-12-09)."""
        p = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": order_type,
             "triggerPrice": str(self.round_price(symbol, trigger)), "quantity": str(self.round_qty(symbol, qty)),
             "workingType": "MARK_PRICE", "reduceOnly": "true", "newOrderRespType": "RESULT"}
        if self.dry:
            print(f"[DRY] ALGO {order_type} {side} {symbol} trig={p['triggerPrice']} qty={p['quantity']}")
            return {"dry": True, **p}
        return self._signed("POST", "/fapi/v1/algoOrder", p)

    def open_algo(self, symbol: str) -> list:
        if self.dry:
            return []
        r = self._signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
        return r if isinstance(r, list) else r.get("orders", [])

    def cancel_algo(self, symbol: str) -> None:
        if self.dry:
            print(f"[DRY] cancel_algo {symbol}")
            return
        for o in self.open_algo(symbol):
            try:
                self._signed("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": str(o["algoId"])})
            except Exception:
                pass
