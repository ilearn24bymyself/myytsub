import glob
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHERS = sorted(glob.glob(str(ROOT / "0[12]_*.bat")))
START_MARKER = "Starting Electron app"

LauncherRun = namedtuple("LauncherRun", "out log_next_to_bat log_in_cwd")


def _run_launcher(bat_src, bootstrap_exit_code, with_venv, cwd_has_venv=False, cwd_is_bat_dir=True):
    """在暫存資料夾複製一份 .bat,配上假的 bootstrap.ps1(可指定結束代碼),
    不碰真正的專案檔案。回傳 LauncherRun(.bat 的完整輸出, startup.log 有沒有落在 .bat 旁邊,
    startup.log 有沒有落在執行當下的資料夾)。
    cwd_is_bat_dir=True 模擬使用者直接雙擊(執行資料夾就是 .bat 所在資料夾);
    False 模擬用捷徑或「以系統管理員身分執行」(執行資料夾是別的地方)。"""
    work = Path(tempfile.mkdtemp(prefix="launcher_a_"))
    other = Path(tempfile.mkdtemp(prefix="launcher_b_"))
    try:
        shutil.copy(bat_src, work / "launcher.bat")
        (work / "bootstrap.ps1").write_text(
            f'Write-Host "[Setup] fake bootstrap"\r\nexit {bootstrap_exit_code}\r\n', encoding="ascii")
        if with_venv:
            (work / "venv").mkdir()
            (work / "venv" / "python.exe").write_bytes(b"")
        if cwd_has_venv:
            (other / "venv").mkdir()
            (other / "venv" / "python.exe").write_bytes(b"")
        result = subprocess.run(
            ["cmd.exe", "/c", str(work / "launcher.bat")],
            cwd=str(work if cwd_is_bat_dir else other), stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
        )
        out = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        return LauncherRun(out, (work / "startup.log").exists(), (other / "startup.log").exists())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)


@unittest.skipUnless(sys.platform == "win32", "啟動用的 .bat 只在 Windows 上有意義")
class LauncherBatTest(unittest.TestCase):
    """使用者在另一台電腦實測:npm install 下載 Electron 失敗(ECONNRESET),
    bootstrap.ps1 已經 exit 1,但 .bat 只檢查 venv\\python.exe 在不在(Python
    那步早就成功了),照樣往下印出 Starting Electron app。"""

    def test_launchers_were_found(self):
        self.assertEqual(len(LAUNCHERS), 2, f"找不到 GPU/CPU 兩個啟動 .bat: {LAUNCHERS}")

    def test_does_not_start_app_when_bootstrap_fails_even_if_python_step_succeeded(self):
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                out = _run_launcher(bat, bootstrap_exit_code=1, with_venv=True).out
                self.assertNotIn(START_MARKER, out)

    def test_does_not_start_app_when_bootstrap_fails_and_nothing_installed(self):
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                out = _run_launcher(bat, bootstrap_exit_code=1, with_venv=False).out
                self.assertNotIn(START_MARKER, out)

    def test_starts_app_when_bootstrap_succeeds(self):
        # 對照組:正常情況不能被擋掉(修完不能變成什麼都不讓啟動)
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                out = _run_launcher(bat, bootstrap_exit_code=0, with_venv=True).out
                self.assertIn(START_MARKER, out)

    def test_starts_app_even_when_launched_from_another_directory(self):
        # 用捷徑或「以系統管理員身分執行」時,執行資料夾不是 .bat 所在資料夾;
        # 檢查必須靠 .bat 自己的位置找檔案,不能靠執行資料夾
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                out = _run_launcher(bat, bootstrap_exit_code=0, with_venv=True, cwd_is_bat_dir=False).out
                self.assertIn(START_MARKER, out)

    def test_ignores_a_venv_that_only_exists_in_the_current_directory(self):
        # .bat 自己的資料夾沒有 venv、只有 cwd 剛好有一個:不能被 cwd 那個蒙混過關
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                out = _run_launcher(bat, bootstrap_exit_code=0, with_venv=False, cwd_has_venv=True,
                                    cwd_is_bat_dir=False).out
                self.assertNotIn(START_MARKER, out)

    def test_the_launch_command_is_one_intact_line(self):
        # 產生/編輯 .bat 時,路徑裡的 \n 很容易被程式語言當成換行,把這一行切成兩半
        expected = rb'"%~dp0node_portable\node.exe" "%~dp0node_modules\electron\cli.js" "%~dp0."'
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                self.assertIn(expected, Path(bat).read_bytes())

    def test_startup_log_is_written_next_to_the_launcher_not_the_current_directory(self):
        # startup.log 要跟專案放一起;寫到「執行當下的資料夾」會亂丟檔案,
        # 那個資料夾唯讀(例如 C:\\Windows\\System32)時甚至會讓後面的指令根本不執行
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                run = _run_launcher(bat, bootstrap_exit_code=0, with_venv=True, cwd_is_bat_dir=False)
                self.assertTrue(run.log_next_to_bat)
                self.assertFalse(run.log_in_cwd)


    def test_launchers_contain_only_ascii_bytes(self):
        # cmd.exe 分塊讀 .bat,UTF-8 的中文字剛好跨過區塊交界就會被讀壞、註解變成假指令
        # (使用者實測看到 "... is not recognized")。讀壞哪一行取決於每個字的精確位置,連資料夾
        # 路徑長度都會影響(同一個檔案短路徑會壞、長路徑正常),所以檔案裡一個非 ASCII 位元組都不能有
        for bat in LAUNCHERS:
            with self.subTest(bat=Path(bat).name):
                data = Path(bat).read_bytes()
                bad = [i for i, b in enumerate(data) if b > 127]
                self.assertEqual(bad, [], f"位元組位置 {bad[:5]} 起有非 ASCII 內容")


if __name__ == "__main__":
    unittest.main()
