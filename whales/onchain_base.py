from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests
from web3 import Web3

from config import ONCHAIN_THRESHOLD_PERCENT
from telegram_notifier import send_telegram
from utils.logger import logger
from whales.redis_db import add_onchain_whale

RPC_URL = "https://mainnet.base.org"
DEX_URL = "https://api.dexscreener.com/latest/dex/search?q=base&chainId=base"
DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={query}"
MIN_LIQUIDITY_USD = 50_000.0
FETCH_TIMEOUT_SEC = 15
POLL_INTERVAL_SEC = 12
REFRESH_TOKENS_SEC = 900
MAX_LOG_BLOCK_SPAN = 12
CONTRACT_FAIL_COOLDOWN_SEC = 1800
MAX_POLL_FAILURES = 6
MAX_FALLBACK_SYMBOLS = 10

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 20}))
TRANSFER_TOPIC = Web3.to_hex(Web3.keccak(text="Transfer(address,address,uint256)"))

failed_tokens: dict[str, float] = {}
_TOKENS_CACHE: List[Dict[str, str]] = []

# Verified Base token contracts from BaseScan pages.
# Keep this list intentionally short and high-confidence, then resolve the rest online.
VERIFIED_FALLBACK_TOKENS: List[Dict[str, str]] = [
    {"symbol": "DEGEN", "address": "0x4ed4e862860bed51a9570b96d89af5e1b0efefed"},
    {"symbol": "AERO", "address": "0x940181a94a35a4569e4529a3cdfb74e38fd98631"},
    {"symbol": "TOSHI", "address": "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4"},
]

# Symbol seeds only. Addresses for these are resolved online from DexScreener on Base
# and then validated on-chain before being used.
FALLBACK_SYMBOL_SEEDS: List[str] = [
    "BRETT",
    "DEGEN",
    "AERO",
    "TOSHI",
    "MOCHI",
    "BALD",
    "TURBO",
    "DOGINME",
    "MOG",
    "PORK",
]

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "None"):
            return default
        return int(value)
    except Exception:
        return default


def _should_skip_symbol(symbol: str) -> bool:
    until = failed_tokens.get(symbol, 0.0)
    return until > time.monotonic()


def _mark_failed(symbol: str, cooldown_sec: int = CONTRACT_FAIL_COOLDOWN_SEC) -> None:
    failed_tokens[symbol] = time.monotonic() + float(cooldown_sec)


def _fetch_json(url: str) -> Dict[str, Any]:
    response = requests.get(url, timeout=FETCH_TIMEOUT_SEC)
    response.raise_for_status()
    return response.json() or {}


def _extract_valid_pairs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pair in list(payload.get("pairs", []) or []):
        if pair.get("chainId") != "base":
            continue
        base_token = pair.get("baseToken") or {}
        symbol = str(base_token.get("symbol") or "").upper().strip()
        address = str(base_token.get("address") or "").strip()
        liquidity_usd = _safe_float((pair.get("liquidity") or {}).get("usd"), 0.0)
        if not symbol or not address or not Web3.is_address(address):
            continue
        out.append(
            {
                "symbol": symbol,
                "address": Web3.to_checksum_address(address),
                "liquidity_usd": liquidity_usd,
                "volume_24h": _safe_float((pair.get("volume") or {}).get("h24"), 0.0),
                "fdv": _safe_float(pair.get("fdv"), 0.0),
                "pair_address": str(pair.get("pairAddress") or ""),
                "dex_id": str(pair.get("dexId") or ""),
            }
        )
    return out


def _fetch_top_25_base_tokens_sync() -> List[Dict[str, str]]:
    data = _fetch_json(DEX_URL)

    tokens: List[Dict[str, str]] = []
    seen: set[str] = set()
    pairs = list(data.get("pairs", []) or [])
    base_pairs = 0
    filtered_liquidity = 0
    filtered_invalid = 0

    for pair in pairs:
        if pair.get("chainId") != "base":
            continue
        base_pairs += 1

        base_token = pair.get("baseToken") or {}
        symbol = str(base_token.get("symbol") or "").upper().strip()
        address = str(base_token.get("address") or "").strip()

        if (
            not symbol
            or not address
            or symbol in seen
            or len(tokens) >= 25
            or not Web3.is_address(address)
        ):
            filtered_invalid += 1
            continue

        liquidity_usd = _safe_float((pair.get("liquidity") or {}).get("usd"), 0.0)
        if liquidity_usd < MIN_LIQUIDITY_USD:
            filtered_liquidity += 1
            continue

        tokens.append({"symbol": symbol, "address": Web3.to_checksum_address(address)})
        seen.add(symbol)

    logger.info(
        "✅ DexScreener Base fetch raw_pairs=%d base_pairs=%d selected=%d filtered_liquidity=%d filtered_invalid=%d",
        len(pairs),
        base_pairs,
        len(tokens),
        filtered_liquidity,
        filtered_invalid,
    )
    return tokens


def _load_contract_runtime(address: str) -> tuple[str, Any, int, float]:
    checksum = Web3.to_checksum_address(address)
    code = w3.eth.get_code(checksum)
    if not code or code == b"" or code == b"\x00":
        raise RuntimeError("NO_CONTRACT_CODE")

    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    decimals = int(contract.functions.decimals().call())
    total_supply_raw = int(contract.functions.totalSupply().call())
    if decimals < 0 or decimals > 36 or total_supply_raw <= 0:
        raise RuntimeError("INVALID_ERC20_METADATA")
    total_supply = float(total_supply_raw) / float(10 ** decimals)
    return checksum, contract, decimals, total_supply


def _resolve_symbol_to_token_sync(symbol: str) -> Optional[Dict[str, str]]:
    q = quote_plus(f"{symbol} chain:base")
    data = _fetch_json(DEX_SEARCH_URL.format(query=q))
    pairs = _extract_valid_pairs(data)
    if not pairs:
        q = quote_plus(symbol)
        data = _fetch_json(DEX_SEARCH_URL.format(query=q))
        pairs = _extract_valid_pairs(data)

    exact_pairs = [p for p in pairs if str(p.get("symbol") or "").upper() == symbol.upper()]
    candidates = exact_pairs or pairs
    candidates = [p for p in candidates if _safe_float(p.get("liquidity_usd"), 0.0) >= MIN_LIQUIDITY_USD]
    if not candidates:
        return None

    candidates.sort(
        key=lambda p: (
            _safe_float(p.get("liquidity_usd"), 0.0),
            _safe_float(p.get("volume_24h"), 0.0),
            _safe_float(p.get("fdv"), 0.0),
        ),
        reverse=True,
    )

    for candidate in candidates:
        address = str(candidate.get("address") or "")
        if not address or not Web3.is_address(address):
            continue
        try:
            checksum, _, _, _ = _load_contract_runtime(address)
            logger.info(
                "✅ Base fallback resolved %s -> %s via DexScreener (liq=$%.0f vol24h=$%.0f dex=%s)",
                symbol,
                checksum,
                _safe_float(candidate.get("liquidity_usd"), 0.0),
                _safe_float(candidate.get("volume_24h"), 0.0),
                str(candidate.get("dex_id") or "-"),
            )
            return {"symbol": symbol.upper(), "address": checksum}
        except Exception as exc:
            logger.warning("⚠️ Base fallback candidate rejected %s %s: %s", symbol, address, str(exc)[:120])
            continue
    return None


def _build_verified_fallback_tokens_sync() -> List[Dict[str, str]]:
    resolved: List[Dict[str, str]] = []
    seen: set[str] = set()

    # First, keep only verified static tokens that still pass on-chain checks.
    for token in VERIFIED_FALLBACK_TOKENS:
        symbol = str(token.get("symbol") or "").upper().strip()
        address = str(token.get("address") or "").strip()
        if not symbol or not address or symbol in seen or _should_skip_symbol(symbol):
            continue
        try:
            checksum, _, _, _ = _load_contract_runtime(address)
            resolved.append({"symbol": symbol, "address": checksum})
            seen.add(symbol)
        except Exception as exc:
            logger.warning("⚠️ Verified Base fallback rejected %s: %s", symbol, str(exc)[:120])

    # Then resolve the rest online by symbol and validate the contract live.
    for symbol in FALLBACK_SYMBOL_SEEDS:
        symbol = str(symbol or "").upper().strip()
        if not symbol or symbol in seen or _should_skip_symbol(symbol):
            continue
        try:
            token = _resolve_symbol_to_token_sync(symbol)
            if token:
                resolved.append(token)
                seen.add(symbol)
            if len(resolved) >= MAX_FALLBACK_SYMBOLS:
                break
        except Exception as exc:
            logger.warning("⚠️ Base fallback lookup gagal %s: %s", symbol, str(exc)[:120])

    return resolved


async def fetch_top_25_base_tokens() -> List[Dict[str, str]]:
    try:
        tokens = await asyncio.to_thread(_fetch_top_25_base_tokens_sync)
        if tokens:
            logger.info("✅ Berhasil fetch %d token viral Base dari DexScreener", len(tokens))
            return tokens
        logger.warning("⚠️ DexScreener Base fetch kosong, memakai fallback manual/online")
    except Exception as exc:
        logger.warning("⚠️ Gagal fetch top tokens dari DexScreener: %s", exc)

    resolved = await asyncio.to_thread(_build_verified_fallback_tokens_sync)
    if resolved:
        logger.info("✅ Base fallback resolved %d token aktif dengan validasi online", len(resolved))
        return resolved

    logger.warning("⚠️ Base fallback online gagal total, memakai verified seed minimum")
    return list(VERIFIED_FALLBACK_TOKENS)


async def _get_tokens_cached(force_refresh: bool = False) -> List[Dict[str, str]]:
    global _TOKENS_CACHE
    if force_refresh or not _TOKENS_CACHE:
        _TOKENS_CACHE = await fetch_top_25_base_tokens()
    return list(_TOKENS_CACHE)


def _get_logs_sync(address: str, from_block: int, to_block: int) -> List[Dict[str, Any]]:
    params = {
        "fromBlock": hex(max(0, int(from_block))),
        "toBlock": hex(max(0, int(to_block))),
        "address": Web3.to_checksum_address(address),
        "topics": [TRANSFER_TOPIC],
    }
    return list(w3.eth.get_logs(params) or [])


async def _emit_whale_alert(symbol: str, percent: float, whale_addr: str, tx_hash: str, contract_address: str) -> None:
    alert = f"""
🐋 **WHALE ALERT - Accumulation Detected!**
Symbol: {symbol}USDT
Accumulated: **{percent:.2f}%** of supply
Wallet: {whale_addr[:8]}...{whale_addr[-6:]}
Tx: https://basescan.org/tx/{tx_hash}
    """.strip()

    await send_telegram(alert, wait_delivery=False, delivery_timeout=10.0)
    await add_onchain_whale(symbol, contract_address, percent, whale_addr, tx_hash)
    logger.info("🐋 Whale Alert terkirim untuk %s (%.2f%%)", symbol, percent)


async def monitor_token(token: Dict[str, str]) -> None:
    symbol = str(token.get("symbol") or "").upper().strip()
    address = str(token.get("address") or "").strip()
    if not symbol or not address:
        return
    if _should_skip_symbol(symbol):
        return

    try:
        checksum, contract, decimals, total_supply = await asyncio.to_thread(_load_contract_runtime, address)
    except Exception as exc:
        logger.warning("⚠️ On-Chain Monitor skip %s: %s", symbol, str(exc)[:160])
        _mark_failed(symbol)
        return

    logger.info("✅ On-Chain Monitor started for %s", symbol)
    failure_count = 0
    latest_block = await asyncio.to_thread(lambda: int(w3.eth.block_number))
    from_block = max(0, latest_block - MAX_LOG_BLOCK_SPAN)

    while True:
        to_block = from_block
        try:
            latest_block = await asyncio.to_thread(lambda: int(w3.eth.block_number))
            if latest_block < from_block:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            to_block = min(latest_block, from_block + MAX_LOG_BLOCK_SPAN - 1)
            logs = await asyncio.to_thread(_get_logs_sync, checksum, from_block, to_block)
            for raw_log in logs:
                try:
                    decoded = contract.events.Transfer().process_log(raw_log)
                    value = float(decoded["args"]["value"]) / float(10 ** decimals)
                    percent = (value / total_supply) * 100.0 if total_supply > 0 else 0.0
                    if percent < float(ONCHAIN_THRESHOLD_PERCENT):
                        continue
                    whale_addr = decoded["args"]["to"]
                    tx_hash = raw_log["transactionHash"].hex() if raw_log.get("transactionHash") else ""
                    await _emit_whale_alert(symbol, percent, whale_addr, tx_hash, checksum)
                except Exception:
                    continue

            from_block = to_block + 1
            failure_count = 0
            await asyncio.sleep(POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure_count += 1
            logger.warning(
                "⚠️ On-Chain Monitor poll error %s blocks=%s-%s: %s",
                symbol,
                from_block,
                to_block,
                str(exc)[:180],
            )
            if failure_count >= MAX_POLL_FAILURES:
                logger.warning("⚠️ On-Chain Monitor disabling %s after repeated poll errors", symbol)
                _mark_failed(symbol)
                return
            await asyncio.sleep(POLL_INTERVAL_SEC)


async def start_onchain_monitor() -> None:
    last_refresh = 0.0
    while True:
        try:
            now = time.monotonic()
            force_refresh = (now - last_refresh) >= REFRESH_TOKENS_SEC or not _TOKENS_CACHE
            tokens = await _get_tokens_cached(force_refresh=force_refresh)
            if force_refresh:
                last_refresh = now
            if not tokens:
                logger.warning("⚠️ On-Chain Base Whale Monitor tidak mendapatkan token aktif")
                await asyncio.sleep(60)
                continue

            active_tokens = [t for t in tokens if not _should_skip_symbol(str(t.get("symbol") or "").upper().strip())]
            logger.info("🌐 On-Chain Base Whale Monitor AKTIF (%d token viral otomatis)", len(active_tokens))
            await asyncio.gather(*(monitor_token(token) for token in active_tokens), return_exceptions=True)
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("⚠️ On-Chain Base monitor supervisor error: %s", str(exc)[:180])
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(start_onchain_monitor())
