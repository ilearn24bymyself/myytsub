import html
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from test_ui_status_display import CHROME  # noqa: E402

PATHS = ["C:/v/有資訊檔.mp4", "C:/v/自動找到.mp4", "C:/v/找不到.mp4"]
META_OK = {"title": "舊影片", "channel": "頻道A", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "upload_date": None}
META_GUESS = {"title": "猜到的", "channel": "頻道B", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "upload_date": None}
PASTED = "https://www.youtube.com/watch?v=zzzzzzzzzzz"

# 選檔視窗換成回傳固定路徑;查完來源後在「找不到」那格貼網址、記下畫面、按開始
STUB = "<script>window.electronAPI = { pickFiles: async () => %s };</script>" % str(PATHS).replace("'", '"')
DRIVER = """<script>
document.getElementById("pick-files").click();
const timer = setInterval(() => {
  const start = document.getElementById("start-btn");
  if (start.disabled) return;
  clearInterval(timer);
  const inputs = document.querySelectorAll(".source-input");
  document.body.dataset.inputs = Array.from(inputs).map((i) => i.value).join("|");
  inputs[inputs.length - 1].value = "%s";
  inputs[inputs.length - 1].dispatchEvent(new Event("input"));
  document.body.dataset.snapshot = document.getElementById("pending-list").innerText;
  start.click();
}, 50);
</script>""" % PASTED


def _serve_and_dump(orch, page, lookup):
    """用真的 index.html + renderer.js 起一個測試伺服器,headless Chrome 跑完後回傳 DOM。"""
    base = server.make_handler(orch)

    class Handler(base):
        def _serve_static(self, path):
            if path in ("/", "/index.html"):
                data = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                super()._serve_static(path)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    profile = tempfile.mkdtemp(prefix="chr_")
    try:
        with mock.patch.object(server, "lookup_source", side_effect=lookup):
            result = subprocess.run(
                [CHROME, "--headless=new", f"--user-data-dir={profile}", "--virtual-time-budget=8000",
                 "--dump-dom", f"http://127.0.0.1:{srv.server_address[1]}/"],
                capture_output=True, timeout=120)
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(profile, ignore_errors=True)
    return result.stdout.decode("utf-8", errors="replace")


def _mock_orchestrator():
    orch = mock.Mock()
    orch.list_jobs.return_value = []
    orch.enqueue_transcription.return_value = "1"
    return orch


def _lookup(path):
    if "有資訊檔" in path:
        return {"status": "sidecar", "metadata": META_OK}
    if "自動找到" in path:
        return {"status": "guessed", "metadata": META_GUESS}
    return {"status": "none", "metadata": None}


@unittest.skipUnless(CHROME, "找不到 Chrome,略過畫面測試")
class PendingListSourceLookupTest(unittest.TestCase):
    """使用者定的流程:加入本機檔案時就查來源;影片旁有資訊檔就直接顯示,反查到的填進網址欄
    標「自動找到,請確認」,真的沒有才由使用者貼網址(留空=本機檔案)。"""

    def test_lookup_results_are_shown_and_the_confirmed_or_pasted_source_is_submitted(self):
        orch = _mock_orchestrator()
        page = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace('<script src="renderer.js"></script>', STUB + '<script src="renderer.js"></script>' + DRIVER)
        self.assertIn(DRIVER, page)
        dom = _serve_and_dump(orch, page, _lookup)

        snapshot = html.unescape(re.search(r'data-snapshot="([^"]*)"', dom).group(1))
        self.assertIn("來源：頻道A / 舊影片（影片旁的資訊檔）", snapshot)
        self.assertIn("自動找到，請確認：頻道B / 猜到的", snapshot)
        self.assertIn("找不到來源，請貼 YouTube 網址（留空＝本機檔案）", snapshot)
        # 只有「自動找到」和「找不到」兩列有網址欄;自動找到的已先填好
        inputs = html.unescape(re.search(r'data-inputs="([^"]*)"', dom).group(1))
        self.assertEqual(inputs, META_GUESS["url"] + "|")

        sent = {c.args[0]["path"]: c.args[0] for c in orch.enqueue_transcription.call_args_list}
        self.assertEqual(sorted(sent), sorted(PATHS))
        self.assertEqual(sent[PATHS[0]].get("metadata"), META_OK)
        self.assertEqual(sent[PATHS[1]].get("metadata"), META_GUESS)   # 沒改 = 確認採用
        self.assertEqual(sent[PATHS[2]].get("source_url"), PASTED)     # 使用者貼的
        self.assertNotIn("metadata", sent[PATHS[2]])

    def test_cleared_files_are_not_looked_up_and_lookups_never_run_in_parallel(self):
        # 還在查來源時按「清空」再重新選檔:清掉的不要再查,兩批也不能同時對 YouTube 發搜尋
        first = ["C:/v/舊1.mp4", "C:/v/舊2.mp4", "C:/v/舊3.mp4"]
        second = ["C:/v/新1.mp4", "C:/v/新2.mp4"]
        stub = """<script>
let pickCount = 0;
window.electronAPI = { pickFiles: async () => (pickCount++ === 0 ? %s : %s) };
</script>""" % (str(first).replace("'", '"'), str(second).replace("'", '"'))
        driver = """<script>
document.getElementById("pick-files").click();
setTimeout(() => {
  document.getElementById("clear-pending-btn").click();
  document.getElementById("pick-files").click();
  const timer = setInterval(() => {
    const start = document.getElementById("start-btn");
    if (start.disabled) return;
    clearInterval(timer);
    start.click();
  }, 50);
}, 20);
</script>"""
        looked_up, active, peak = [], [0], [0]
        lock = threading.Lock()

        def slow_lookup(path):
            with lock:
                looked_up.append(path)
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            with lock:
                active[0] -= 1
            return {"status": "none", "metadata": None}

        orch = _mock_orchestrator()
        page = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace('<script src="renderer.js"></script>', stub + '<script src="renderer.js"></script>' + driver)
        _serve_and_dump(orch, page, slow_lookup)

        # 舊那批至少有一支真的已經開始查(清空時查詢還在進行),否則這個測試等於沒測到「重疊」的情況
        self.assertTrue(any(p in first for p in looked_up), looked_up)
        self.assertEqual(peak[0], 1, looked_up)
        self.assertNotIn(first[2], looked_up)                 # 清掉之後還沒輪到的不查
        self.assertEqual([p for p in looked_up if p in second], second)
        sent = [c.args[0]["path"] for c in orch.enqueue_transcription.call_args_list]
        self.assertEqual(sent, second)


if __name__ == "__main__":
    unittest.main()
