from __future__ import annotations

import os
import urllib.parse
import urllib.request


def send(text: str, token: str | None = None, chat_id: str | None = None) -> bool:
    token = token or os.getenv("FUSIONWHALE_BOT_TOKEN")
    chat_id = chat_id or os.getenv("FUSIONWHALE_CHAT_ID")
    if not token or not chat_id:
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10) as r:
        return r.status == 200
