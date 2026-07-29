import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from signal_copy.telegram_transport import _send_with_retry


class TelegramRetryTest(unittest.TestCase):
    def test_transient_failure_retries_then_succeeds(self):
        send = AsyncMock(side_effect=[TimeoutError("slow"), None])
        with patch("signal_copy.telegram_transport.asyncio.sleep", new=AsyncMock()):
            ok = asyncio.run(_send_with_retry(send, attempts=3))
        self.assertTrue(ok)
        self.assertEqual(send.await_count, 2)

    def test_permanent_failure_stops_after_attempt_limit(self):
        send = AsyncMock(side_effect=TimeoutError("slow"))
        with patch("signal_copy.telegram_transport.asyncio.sleep", new=AsyncMock()):
            ok = asyncio.run(_send_with_retry(send, attempts=3))
        self.assertFalse(ok)
        self.assertEqual(send.await_count, 3)


if __name__ == "__main__":
    unittest.main()
