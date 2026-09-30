import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
from orchestrator import JobState, Orchestrator, RateLimited  # noqa: E402


def _find_chrome():
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    return next((str(c) for c in candidates if c.is_file()), None)


CHROME = _find_chrome()


def _wait_until(condition, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(CHROME, "找不到 Chrome,略過畫面測試")
class JobTableDisplayTest(unittest.TestCase):
    """用真的 orchestrator、真的 HTTP 服務、真的 renderer.js(交給無頭 Chrome 渲染,不開 Electron
    視窗),檢查畫面上「狀態 / 進度」欄實際顯示的文字。使用者實測看到「完成」底下還寫「下載中」。"""

    def test_status_column_shows_the_right_text_for_each_kind_of_job(self):
        hold = threading.Event()

        def download_fn(payload, cancel_event, pause_event, report_progress):
            if payload == "limit-hit":
                raise RateLimited("429")
            report_progress(40.0, "下載中")
            report_progress(100.0, "下載完成", final=True)

        def transcribe_fn(payload, cancel_event, pause_event, report_progress):
            report_progress(30.0, "轉錄中")
            if payload == "long":
                hold.wait(30)
            else:
                report_progress(100.0, "轉錄完成", final=True)

        orch = Orchestrator(download_fn, transcribe_fn)
        ids = {
            "ok": orch.enqueue_download("ok"),
            "hit": orch.enqueue_download("limit-hit"),
            "skipped": orch.enqueue_download("skipped-by-limit"),
            "quick": orch.enqueue_transcription("done-quick"),
            "long": orch.enqueue_transcription("long"),
        }
        self.assertTrue(_wait_until(lambda: all(
            orch.get_job(ids[k]).state in (JobState.DONE, JobState.PENDING_RETRY)
            for k in ("ok", "hit", "skipped", "quick"))))
        self.assertTrue(_wait_until(lambda: orch.get_job(ids["long"]).state == JobState.RUNNING
                                    and orch.get_job(ids["long"]).started_at is not None))

        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(orch))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        profile = tempfile.mkdtemp(prefix="chr_")
        try:
            result = subprocess.run(
                [CHROME, "--headless=new", f"--user-data-dir={profile}", "--virtual-time-budget=6000",
                 "--dump-dom", f"http://127.0.0.1:{srv.server_address[1]}/"],
                capture_output=True, timeout=120)
            html = result.stdout.decode("utf-8", errors="replace")
        finally:
            hold.set()
            srv.shutdown()
            srv.server_close()
            orch.wait_idle()
            orch.shutdown()
            shutil.rmtree(profile, ignore_errors=True)

        status = {}
        for row in re.findall(r"<tr>(.*?)</tr>", html.split("<tbody>")[1], re.S):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            status[cells[1]] = cells[4]

        finished_ok, hit, skipped, quick, running = (status[ids[k]] for k in ("ok", "hit", "skipped", "quick", "long"))
        self.assertIn("下載完成", finished_ok)
        self.assertNotIn("下載中", finished_ok)
        self.assertIn("轉錄完成", quick)
        self.assertNotIn("轉錄中", quick)
        self.assertTrue("待重試" in hit and "限流" in hit, hit)
        self.assertTrue("待重試" in skipped and "限流" in skipped, skipped)
        self.assertIn("執行中", running)
        self.assertIn("%", running)
        self.assertIsNotNone(re.search(r"已執行 \d+:\d\d", running), running)

    def test_a_paused_job_says_paused_and_stops_counting_elapsed_time(self):
        # 使用者實測:按暫停後畫面還是「執行中」、已執行時間照跑,分不出是暫停還是卡住
        hold = threading.Event()

        def transcribe_fn(payload, cancel_event, pause_event, report_progress):
            report_progress(30.0, "轉錄中")
            hold.wait(30)

        orch = Orchestrator(lambda *a: None, transcribe_fn)
        job_id = orch.enqueue_transcription("long")
        self.assertTrue(_wait_until(lambda: orch.get_job(job_id).state == JobState.RUNNING
                                    and orch.get_job(job_id).started_at is not None))
        orch.pause(job_id)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(orch))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        profile = tempfile.mkdtemp(prefix="chr_")
        try:
            result = subprocess.run(
                [CHROME, "--headless=new", f"--user-data-dir={profile}", "--virtual-time-budget=4000",
                 "--dump-dom", f"http://127.0.0.1:{srv.server_address[1]}/"],
                capture_output=True, timeout=120)
            html = result.stdout.decode("utf-8", errors="replace")
        finally:
            hold.set()
            srv.shutdown()
            srv.server_close()
            orch.wait_idle()
            orch.shutdown()
            shutil.rmtree(profile, ignore_errors=True)

        row = re.findall(r"<tr>(.*?)</tr>", html.split("<tbody>")[1], re.S)[0]
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        status, actions = cells[4], cells[6]
        self.assertIn("已暫停", status)
        self.assertNotIn("已執行", status)
        self.assertIn("繼續", actions)
        self.assertIn("取消", actions)


if __name__ == "__main__":
    unittest.main()
