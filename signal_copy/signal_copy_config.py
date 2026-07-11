"""
Minimal config for signal_copy pipeline in nexus.

Secrets are read from environment variables so nothing sensitive is hard-coded.
Set them in fusion/.env (already loaded by config.py via python-dotenv) or the
process environment.

Required to actually run:
- Telegram source reading (choose ONE):
    TELETHON path (reads ANY joined group):
        SIGNAL_COPY_TG_API_ID, SIGNAL_COPY_TG_API_HASH
    OR bot path (only chats where the bot is admin):
        SIGNAL_COPY_TG_LISTENER_BOT_TOKEN
- Parser/Validation notifications (VALID/WEAK/REJECT):
    SIGNAL_COPY_PARSER_NOTIFY_BOT_TOKEN, SIGNAL_COPY_PARSER_NOTIFY_CHAT_ID
- Execution/Trades notifications (entry, TP, SL, close):
    SIGNAL_COPY_TRADES_NOTIFY_BOT_TOKEN, SIGNAL_COPY_TRADES_NOTIFY_CHAT_ID
- Discord (optional):
    SIGNAL_COPY_DISCORD_BOT_TOKEN
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # idempotent; ensures .env is available regardless of import order
except Exception:
    pass


def _int(name: str, default: int = 0) -> int:
    try:
        v = os.getenv(name, "").strip()
        return int(v) if v else default
    except Exception:
        return default


def _ids(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


# --- Telegram source (Telethon user-account backend, preferred) ---
TG_API_ID = _int("SIGNAL_COPY_TG_API_ID", 0)
TG_API_HASH = os.getenv("SIGNAL_COPY_TG_API_HASH", "").strip()
TG_SESSION_NAME = os.getenv("SIGNAL_COPY_TG_SESSION", "signal_copy_session").strip()

# --- Telegram source (aiogram bot backend, fallback) ---
TG_LISTENER_BOT_TOKEN = os.getenv("SIGNAL_COPY_TG_LISTENER_BOT_TOKEN", "").strip()

# Allowlist of Telegram chat ids to read signals from (empty = all joined).
TG_SIGNAL_CHANNELS = _ids("SIGNAL_COPY_TG_CHANNELS")
TG_CHANNEL_NAMES: dict[int, str] = {}  # optional id->name mapping for nicer labels

# --- Parser notifications channel (validation reports: VALID/WEAK/REJECT) ---
PARSER_NOTIFY_BOT_TOKEN = os.getenv("SIGNAL_COPY_PARSER_NOTIFY_BOT_TOKEN", "").strip()
PARSER_NOTIFY_CHAT_ID = _int("SIGNAL_COPY_PARSER_NOTIFY_CHAT_ID", 0)

# --- Execution/Trades notifications channel (entry, TP, SL, close) ---
TRADES_NOTIFY_BOT_TOKEN = os.getenv("SIGNAL_COPY_TRADES_NOTIFY_BOT_TOKEN", "").strip()
TRADES_NOTIFY_CHAT_ID = _int("SIGNAL_COPY_TRADES_NOTIFY_CHAT_ID", 0)

# --- Calibration sandbox (Tahap 1/2 testing; READ-ONLY, never executes) ---
# A separate Telegram bot you forward test signals to; it replies with exactly
# what the bot understood (classification + fields + chart-vision read).
CALIB_BOT_TOKEN = os.getenv("SIGNAL_COPY_CALIB_BOT_TOKEN", "").strip()
# Discord channel ids treated as read-only calibration intake (report only).
CALIB_DISCORD_CHANNELS = _ids("SIGNAL_COPY_CALIB_DISCORD_CHANNELS")

# --- Discord source ---
DISCORD_ENABLED = _bool("SIGNAL_COPY_DISCORD_ENABLED", False)
DISCORD_BOT_TOKEN = os.getenv("SIGNAL_COPY_DISCORD_BOT_TOKEN", "").strip()
# Self-bot (user account) mode: reads servers you are a member of. Requires
# discord.py-self and your USER token. NOTE: violates Discord ToS (ban risk).
DISCORD_SELFBOT = _bool("SIGNAL_COPY_DISCORD_SELFBOT", False)
DISCORD_USER_TOKEN = os.getenv("SIGNAL_COPY_DISCORD_USER_TOKEN", "").strip()
DISCORD_CHANNELS = _ids("SIGNAL_COPY_DISCORD_CHANNELS")
DISCORD_CHANNEL_NAMES: dict[int, str] = {}
# Server (guild) ids of interest — used to log the real channel/thread ids of
# incoming messages so the allowlist can be fixed for forum/thread channels,
# and to explicitly subscribe (discord.py-self lazy-guild workaround).
DISCORD_GUILDS = _ids("SIGNAL_COPY_DISCORD_GUILDS")
# REST polling fallback for watched channels (robust against gateway lazy-load).
# Empty -> defaults to ON in selfbot mode, OFF in bot mode.
_dp = os.getenv("SIGNAL_COPY_DISCORD_POLL", "").strip().lower()
DISCORD_POLL_ENABLED = None if not _dp else _dp in {"1", "true", "yes", "y", "on"}
DISCORD_POLL_INTERVAL = float(os.getenv("SIGNAL_COPY_DISCORD_POLL_INTERVAL", "25"))


def discord_token() -> str:
    """Active Discord token: user token in selfbot mode, else bot token."""
    return DISCORD_USER_TOKEN if DISCORD_SELFBOT else DISCORD_BOT_TOKEN


# --- Behavior ---
RISK_PCT = float(os.getenv("SIGNAL_COPY_RISK_PCT", "0.01"))   # 1% of equity per trade
CONFIRM_EXPIRY_SEC = float(os.getenv("SIGNAL_COPY_CONFIRM_EXPIRY_SEC", "600"))  # 10 min
AUTO_EXECUTE_WITHOUT_CONFIRM = _bool("SIGNAL_COPY_AUTO_EXECUTE", False)  # if True, skip yes/no
DRY_RUN = _bool("SIGNAL_COPY_DRY_RUN", False)   # validate + confirm but never place orders
NOTIFY_REJECTED = _bool("SIGNAL_COPY_NOTIFY_REJECTED", True)  # tell user about rejects too
NOTIFY_WEAK = _bool("SIGNAL_COPY_NOTIFY_WEAK", True)
# Tahap 1: push the detailed per-message "read report" to Telegram (calibration).
PARSE_REPORT = _bool("SIGNAL_COPY_PARSE_REPORT", False)
# Surface detected whale-accumulation narratives (no trading yet).
NOTIFY_ACCUM = _bool("SIGNAL_COPY_NOTIFY_ACCUM", False)

# Legacy validation mode (permissive, mirrors old bot behavior)
LEGACY_VALIDATION = _bool("SIGNAL_COPY_LEGACY_VALIDATION", False)

# Dedup window: ignore identical signals (same symbol+side+entry) within N seconds.
DEDUP_WINDOW_SEC = float(os.getenv("SIGNAL_COPY_DEDUP_WINDOW_SEC", "1800"))

# --- Vision (Tahap 2): read the chart/outlook image attached to a signal ---
VISION_ENABLED = _bool("SIGNAL_COPY_VISION_ENABLED", False)
# "ollama" (local model, free) or "n8n" (webhook to your n8n flow).
VISION_BACKEND = os.getenv("SIGNAL_COPY_VISION_BACKEND", "ollama").strip().lower()
OLLAMA_URL = os.getenv("SIGNAL_COPY_OLLAMA_URL", "http://localhost:11434").strip()
VISION_MODEL = os.getenv("SIGNAL_COPY_VISION_MODEL", "qwen2.5vl:3b").strip()
VISION_TIMEOUT_SEC = float(os.getenv("SIGNAL_COPY_VISION_TIMEOUT_SEC", "300"))
N8N_WEBHOOK_URL = os.getenv("SIGNAL_COPY_N8N_WEBHOOK_URL", "").strip()
# OpenAI-compatible cloud backend (OpenRouter / Google Gemini openai-endpoint /
# OpenAI / similar). Fast + offloads the VPS CPU. Set BACKEND=openai to use.
VISION_OPENAI_BASE_URL = os.getenv("SIGNAL_COPY_VISION_OPENAI_BASE_URL", "").strip()
VISION_OPENAI_API_KEY = os.getenv("SIGNAL_COPY_VISION_OPENAI_API_KEY", "").strip()
VISION_OPENAI_MODEL = os.getenv("SIGNAL_COPY_VISION_OPENAI_MODEL", "gpt-4o-mini").strip()
# If the cloud backend is rate-limited/unavailable, fall back to local Ollama.
VISION_FALLBACK_OLLAMA = _bool("SIGNAL_COPY_VISION_FALLBACK_OLLAMA", True)

# --- Adversarial gate (Tahap 3): bull/bear debate before entry ---
ADVERSARIAL_ENABLED = _bool("SIGNAL_COPY_ADVERSARIAL_ENABLED", True)

# --- VIP Fast Lane / Silent Accumulation (Tahap 4) ---
VIP_FAST_LANE_ENABLED = _bool("SIGNAL_COPY_VIP_FAST_LANE_ENABLED", False)

# --- SMC Engine (OB + FVG) ---
SMC_ENABLED = _bool("SIGNAL_COPY_SMC_ENABLED", False)


def summary() -> str:
    return (
        f"signal_copy config: "
        f"tg_telethon={'on' if (TG_API_ID and TG_API_HASH) else 'off'} "
        f"tg_bot={'on' if TG_LISTENER_BOT_TOKEN else 'off'} "
        f"parser_notify={'on' if PARSER_NOTIFY_BOT_TOKEN and PARSER_NOTIFY_CHAT_ID else 'off'} "
        f"trades_notify={'on' if TRADES_NOTIFY_BOT_TOKEN and TRADES_NOTIFY_CHAT_ID else 'off'} "
        f"discord={'on' if DISCORD_ENABLED and discord_token() else 'off'}"
        f"{'(selfbot)' if DISCORD_SELFBOT else ''} "
        f"risk={RISK_PCT*100:.2f}% dry_run={DRY_RUN} auto={AUTO_EXECUTE_WITHOUT_CONFIRM}"
    )