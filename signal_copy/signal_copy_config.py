"""
Minimal config for signal_copy pipeline in nexus.
"""

# Signal sources (Telegram channel IDs to monitor)
TG_SIGNAL_CHANNELS = []  # Empty = learning mode (accept all, log channel IDs)

# Confirmation
CONFIRM_EXPIRY_SEC = 300  # 5 minutes
AUTO_EXECUTE_WITHOUT_CONFIRM = False  # Require manual confirm

# Deduplication
DEDUP_WINDOW_SEC = 600  # 10 minutes

# Risk
RISK_PCT = 1.0  # 1% risk per trade

# Execution
DRY_RUN = True  # Safe default - no real orders

# Features
ADVERSARIAL_ENABLED = True  # Enable bull/bear debate
VISION_ENABLED = False  # Chart vision (requires GPT-4V)
PARSE_REPORT = False  # Detailed parse reports

# Notification (stub - will wire Telegram later)
TELEGRAM_NOTIFY_ENABLED = False
