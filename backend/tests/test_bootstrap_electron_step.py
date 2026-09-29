import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 預設測專案現在的 bootstrap.ps1;想確認「弄壞之後會紅」時可以指到改壞的副本
BOOTSTRAP = Path(os.environ.get("BOOTSTRAP_UNDER_TEST", ROOT / "bootstrap.ps1"))

# 假的 npm,盡量貼近真的行為:
# - 第 SUCCEED_ON 次呼叫才成功,之前每次都失敗,而且失敗時留下「有資料夾、沒有執行檔」的半成品
# - 套件資料夾已經在就當作已安裝,直接成功、不會重新下載執行檔(真的 npm 就是這樣)
FAKE_NPM = r"""@echo off
set N=0
if exist "%~dp0calls.txt" set /p N=<"%~dp0calls.txt"
set /a N+=1
>"%~dp0calls.txt" echo %N%
rem like real npm: an existing package folder counts as installed, the binary is not fetched again
if exist "%~dp0..\node_modules\electron" (
  echo fake npm: electron folder already present, nothing to do
  exit /b 0
)
if %N% LSS %SUCCEED_ON% (
  mkdir "%~dp0..\node_modules\electron"
  echo fake npm: simulated ECONNRESET on call %N%
  exit /b 1
)
mkdir "%~dp0..\node_modules\electron\dist"
type nul > "%~dp0..\node_modules\electron\dist\electron.exe"
exit /b 0
"""


def _run_bootstrap(succeed_on: int, electron_state=None):
    """把 Python / Node / ffmpeg 那幾步都預先「裝好」讓它們跳過,只留下 Electron 那一步
    對著假 npm 跑真的 bootstrap.ps1。electron_state: None=完全沒裝、"half"=只有資料夾沒有
    執行檔、"full"=已經裝好。回傳 (結束代碼, 輸出文字, npm 被呼叫幾次, electron.exe 在不在)。"""
    root = Path(tempfile.mkdtemp(prefix="boot_"))
    try:
        shutil.copy(BOOTSTRAP, root / "bootstrap.ps1")
        helpers = ROOT / "bootstrap_helpers.ps1"
        if helpers.exists():
            shutil.copy(helpers, root / "bootstrap_helpers.ps1")
        (root / "backend" / "bin").mkdir(parents=True)
        (root / "backend" / "bin" / "ffmpeg.exe").write_bytes(b"")
        (root / "backend" / "bin" / "ffprobe.exe").write_bytes(b"")
        (root / "venv" / "Lib" / "site-packages" / "faster_whisper").mkdir(parents=True)
        (root / "venv" / "python.exe").write_bytes(b"")
        (root / "venv" / "_installed_flavor.txt").write_text("GPU\r\n", encoding="ascii")
        (root / "node_portable").mkdir()
        (root / "node_portable" / "node.exe").write_bytes(b"")
        (root / "node_portable" / "npm.cmd").write_text(FAKE_NPM, encoding="ascii")
        if electron_state in ("half", "full"):
            (root / "node_modules" / "electron").mkdir(parents=True)
            (root / "node_modules" / "electron" / "package.json").write_text("{}", encoding="ascii")
        if electron_state == "full":
            (root / "node_modules" / "electron" / "dist").mkdir()
            (root / "node_modules" / "electron" / "dist" / "electron.exe").write_bytes(b"")

        env = dict(os.environ, SUCCEED_ON=str(succeed_on), BOOTSTRAP_RETRY_DELAY="0")
        env.pop("FORCE_CPU", None)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "bootstrap.ps1")],
            cwd=str(root), env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
        )
        out = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        calls_file = root / "node_portable" / "calls.txt"
        calls = int(calls_file.read_text().strip()) if calls_file.exists() else 0
        exe_ok = (root / "node_modules" / "electron" / "dist" / "electron.exe").exists()
        return result.returncode, out, calls, exe_ok
    finally:
        shutil.rmtree(root, ignore_errors=True)


@unittest.skipUnless(sys.platform == "win32", "bootstrap.ps1 只在 Windows 上有意義")
class ElectronInstallStepTest(unittest.TestCase):
    """使用者在另一台電腦實測:npm install 下載 Electron 途中連線被重設,整個安裝直接放棄。"""

    def test_recovers_when_npm_fails_twice_then_succeeds(self):
        # 前兩次失敗都會留下半成品資料夾;第 2、3 次能成功,代表重試前有把半成品清掉
        code, out, calls, exe_ok = _run_bootstrap(succeed_on=3)
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, 3)
        self.assertTrue(exe_ok)

    def test_reports_failure_with_nonzero_exit_code_when_npm_never_succeeds(self):
        code, out, calls, exe_ok = _run_bootstrap(succeed_on=99)
        self.assertEqual(code, 1, out)
        self.assertEqual(calls, 3)
        self.assertFalse(exe_ok)
        self.assertIn("[Setup][ERROR]", out)

    def test_half_installed_folder_from_an_earlier_run_is_reinstalled_not_skipped(self):
        # 上一次失敗留下「有 node_modules\\electron 資料夾、沒有 dist\\electron.exe」的半成品。
        # 只看資料夾在不在會誤判成已安裝而跳過,執行檔永遠不會補下載
        code, out, calls, exe_ok = _run_bootstrap(succeed_on=1, electron_state="half")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, 1)
        self.assertTrue(exe_ok)

    def test_does_not_reinstall_when_the_electron_binary_is_already_there(self):
        # 對照組:真的裝好了就不能重跑 npm(否則每次啟動都重新下載 100MB)
        code, out, calls, exe_ok = _run_bootstrap(succeed_on=1, electron_state="full")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, 0)
        self.assertTrue(exe_ok)


if __name__ == "__main__":
    unittest.main()
