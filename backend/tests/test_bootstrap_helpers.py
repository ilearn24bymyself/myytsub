import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HELPERS = Path(__file__).resolve().parents[2] / "bootstrap_helpers.ps1"


def _run_ps(body: str) -> str:
    """把 body 放進一支暫存 .ps1,先 dot-source bootstrap_helpers.ps1 再執行,回傳最後一行輸出。"""
    script = f'. "{HELPERS}"\r\n{body}\r\n'
    with tempfile.TemporaryDirectory() as tmp:
        ps1 = Path(tmp) / "t.ps1"
        ps1.write_text(script, encoding="ascii")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
        )
    lines = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else ""


@unittest.skipUnless(sys.platform == "win32", "bootstrap.ps1 只在 Windows 上有意義")
class InvokeWithRetryTest(unittest.TestCase):
    """使用者在另一台電腦實測:下載 Electron 途中連線被重設(ECONNRESET)。網路瞬斷很常見,
    不該一次失敗就整個安裝放棄——同一個動作要能自動再試幾次。"""

    def test_retries_until_it_succeeds_within_the_limit(self):
        out = _run_ps(
            '$script:calls = 0\r\n'
            '$ok = Invoke-WithRetry -Label "t" -MaxAttempts 3 -DelaySeconds 0 -Attempt { $script:calls++; $script:calls -ge 3 }\r\n'
            'Write-Output "ok=$ok calls=$($script:calls)"')
        self.assertEqual(out, "ok=True calls=3")

    def test_gives_up_after_the_max_attempts(self):
        out = _run_ps(
            '$script:calls = 0\r\n'
            '$ok = Invoke-WithRetry -Label "t" -MaxAttempts 3 -DelaySeconds 0 -Attempt { $script:calls++; $false }\r\n'
            'Write-Output "ok=$ok calls=$($script:calls)"')
        self.assertEqual(out, "ok=False calls=3")

    def test_an_exception_counts_as_a_failed_attempt_not_a_crash(self):
        out = _run_ps(
            '$script:calls = 0\r\n'
            '$ok = Invoke-WithRetry -Label "t" -MaxAttempts 3 -DelaySeconds 0 -Attempt { $script:calls++; if ($script:calls -lt 2) { throw "boom" }; $true }\r\n'
            'Write-Output "ok=$ok calls=$($script:calls)"')
        self.assertEqual(out, "ok=True calls=2")

    def test_does_not_retry_when_the_first_attempt_succeeds(self):
        out = _run_ps(
            '$script:calls = 0\r\n'
            '$ok = Invoke-WithRetry -Label "t" -MaxAttempts 3 -DelaySeconds 0 -Attempt { $script:calls++; $true }\r\n'
            'Write-Output "ok=$ok calls=$($script:calls)"')
        self.assertEqual(out, "ok=True calls=1")


if __name__ == "__main__":
    unittest.main()
