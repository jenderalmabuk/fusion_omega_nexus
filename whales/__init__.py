"""
whales package - loads compatibility shim first so submodules can import
from config, telegram_notifier, utils.logger (legacy fusion_xomegabot names).
"""
import whales_compat  # noqa: F401 - installs config, telegram_notifier, utils.logger into sys.modules