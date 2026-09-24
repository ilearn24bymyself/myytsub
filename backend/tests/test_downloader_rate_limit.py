import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from downloader import is_rate_limited_error  # noqa: E402


class RateLimitDetectionTest(unittest.TestCase):
    def test_recognizes_known_rate_limit_and_bot_check_signals(self):
        rate_limited_examples = [
            "HTTP Error 429: Too Many Requests",
            "ERROR: [youtube] abc123: Sign in to confirm you're not a bot",
            "urlopen error [Errno 429]",
        ]
        for msg in rate_limited_examples:
            with self.subTest(msg=msg):
                self.assertTrue(is_rate_limited_error(msg))

    def test_does_not_flag_unrelated_errors(self):
        unrelated_examples = [
            "WinError 32: 程序無法存取檔案，因為檔案正由另一個處理程序使用中。",
            "This video is unavailable",
            "[Errno 11001] getaddrinfo failed",
        ]
        for msg in unrelated_examples:
            with self.subTest(msg=msg):
                self.assertFalse(is_rate_limited_error(msg))


if __name__ == "__main__":
    unittest.main()
