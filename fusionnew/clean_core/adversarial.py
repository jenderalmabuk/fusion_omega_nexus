"""
Adversarial entry check — bull vs bear debate before committing a trade.
Inspired by TradingAgents multi-agent architecture.

Usage:
    from clean_core.adversarial import bull_bear_check
    ok, reason = bull_bear_check(symbol, setup_dict)
    if not ok:
        skip entry — bear argument wins
"""
import json, os, time
from typing import Dict, Any, Tuple

# LLM config — use same provider as main config or env
LLM_API_KEY = os.getenv("NINE_ROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("CUSTOM_API_KEY") or ""
LLM_MODEL_PRIMARY = os.getenv("ADVERSARIAL_MODEL", "gc/gemini-2.5-pro")
LLM_MODEL_FALLBACK = os.getenv("ADVERSARIAL_MODEL_FALLBACK", "gc/gemini-2.5-pro")
LLM_BASE = os.getenv("NINE_ROUTER_BASE") or os.getenv("OPENROUTER_BASE") or "http://127.0.0.1:20128/v1"

# Track which model is currently active (starts with primary, falls back on error)
_current_model = LLM_MODEL_PRIMARY
_fallback_active = False

BULL_PROMPT = """You are a bull analyst. Given this trading setup, argue why this is a GOOD entry.
Focus on: technical confluence, momentum, volume, pattern completion, risk/reward.
Setup:
{symbol} {side} on {tier}
Entry: {entry} | SL: {sl} | TP: {tp} | RR: 1:{rr}
Imbalance: {imb_side} | Complete: {t_complete}
Keep response under 80 words. Be specific about what confirms this trade."""

BEAR_PROMPT = """You are a bear analyst. Given this trading setup, argue why this is a BAD entry.
Focus on: potential fakeout, resistance, low conviction, conflicting signals, bad timing.
Setup:
{symbol} {side} on {tier}
Entry: {entry} | SL: {sl} | TP: {tp} | RR: 1:{rr}
Imbalance: {imb_side} | Complete: {t_complete}
Keep response under 80 words. Be specific about what invalidates this trade."""

JUDGE_PROMPT = """You are a trade judge. Two analysts debated this setup:

BULL: {bull_arg}
BEAR: {bear_arg}

Decide: should we take this trade? Reply ONLY with YES or NO. No other text."""


def _call_llm(prompt: str) -> str:
    """Call LLM via OpenRouter-compatible API with fallback."""
    global _current_model, _fallback_active
    import requests
    if not LLM_API_KEY:
        return "NO_API_KEY"
    # Try primary first, then fallback on any error
    models_to_try = (
        [LLM_MODEL_PRIMARY, LLM_MODEL_FALLBACK]
        if LLM_MODEL_PRIMARY != LLM_MODEL_FALLBACK
        else [LLM_MODEL_PRIMARY]
    )
    for model in models_to_try:
        try:
            resp = requests.post(
                f"{LLM_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 120,
                    "temperature": 0.3,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                text = resp.text.strip()
                if text.startswith("data:"):
                    chunks = []
                    for line in text.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        obj = json.loads(payload)
                        choice = obj.get("choices", [{}])[0]
                        delta = choice.get("delta") or {}
                        msg = choice.get("message") or {}
                        if "content" in delta:
                            chunks.append(delta["content"])
                        elif "content" in msg:
                            chunks.append(msg["content"])
                    result = "".join(chunks).strip()
                else:
                    # Gateway may append "data: [DONE]" or newlines after JSON.
                    text = text.split("data: [DONE]")[0].strip()
                    data = json.loads(text)
                    result = data["choices"][0]["message"]["content"].strip()
                # Success — promote back to primary if we were on fallback
                if _fallback_active and model == LLM_MODEL_PRIMARY:
                    _fallback_active = False
                _current_model = model
                return result
            # Non-200: try next model
        except Exception:
            # Exception: try next model
            pass
    # All models failed
    return "ERR:all_models_failed"


def adversarial_model_status() -> str:
    """Return current model name and whether fallback is active."""
    return f"{_current_model}{' (fallback)' if _fallback_active else ''}"


def bull_bear_check(symbol: str, s: Dict[str, Any]) -> Tuple[bool, str]:
    """Run bull vs bear debate. Returns (ok, reason)."""
    global _current_model, _fallback_active
    if not LLM_API_KEY:
        # No LLM configured — allow all trades (default behavior)
        return True, "no_llm"

    tier = s.get("tier", "M30")
    side = s.get("side", "BULL")
    entry = s.get("entry", 0)
    sl = s.get("sl", 0)
    tp = s.get("tp", 0)
    imb_side = s.get("imb_side", "?")
    t_complete = s.get("t_complete", "?")

    fmt = dict(
        symbol=symbol, side=side, tier=tier,
        entry=f"{entry:.6g}", sl=f"{sl:.6g}", tp=f"{tp:.6g}",
        rr=2, imb_side=imb_side, t_complete=str(t_complete)[:19],
    )

    bull_arg = _call_llm(BULL_PROMPT.format(**fmt))
    if bull_arg.startswith("ERR") or bull_arg.startswith("HTTP"):
        # If primary failed, mark fallback and retry once
        if not _fallback_active and LLM_MODEL_PRIMARY != LLM_MODEL_FALLBACK:
            _fallback_active = True
            _current_model = LLM_MODEL_FALLBACK
            bull_arg = _call_llm(BULL_PROMPT.format(**fmt))
        if bull_arg.startswith("ERR") or bull_arg.startswith("HTTP"):
            return True, f"llm_err:{bull_arg}"

    bear_arg = _call_llm(BEAR_PROMPT.format(**fmt))
    if bear_arg.startswith("ERR") or bear_arg.startswith("HTTP"):
        return True, f"llm_err:{bear_arg}"

    judge = _call_llm(JUDGE_PROMPT.format(bull_arg=bull_arg, bear_arg=bear_arg))
    ok = judge.upper().startswith("YES")
    return ok, judge[:100]
