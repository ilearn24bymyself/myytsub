import re
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from orchestrator import JobState, Orchestrator  # noqa: E402
from test_ui_source_lookup import _serve_and_dump  # noqa: E402
from test_ui_status_display import CHROME, _wait_until  # noqa: E402

# 表格有 4 列:1 個已完成的下載(沒有勾選框)、1 個執行中、2 個排隊中的轉錄(這三個才能取消)
DRIVER = """<script>
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const boxes = () => Array.from(document.querySelectorAll("[data-select]"));
const checkedCount = () => boxes().filter((b) => b.checked).length;
(async () => {
  while (document.querySelectorAll("#jobs-table tbody tr").length < 4) await wait(50);
  const all = document.getElementById("select-all");
  const d = document.body.dataset;
  d.total = boxes().length;
  all.click(); await wait(50);
  d.selectall = checkedCount();
  d.cancelenabled = !document.getElementById("cancel-selected-btn").disabled;
  await wait(2000);                       // 中間會輪詢重畫一次,勾選不能被清掉
  d.afterpoll = checkedCount();
  d.headafterpoll = document.getElementById("select-all").checked;
  document.getElementById("select-all").click(); await wait(50);
  d.selectnone = checkedCount();
  boxes()[0].click(); await wait(50);     // 自己勾一個:全選框不能還是「全選」的狀態
  d.headafterone = document.getElementById("select-all").checked;
  d.finished = "yes";
})();
</script>"""


@unittest.skipUnless(CHROME, "找不到 Chrome,略過畫面測試")
class SelectAllTest(unittest.TestCase):
    def test_header_checkbox_selects_and_clears_every_cancellable_row(self):
        hold = threading.Event()

        def transcribe_fn(payload, cancel_event, pause_event, report_progress):
            hold.wait(30)

        orch = Orchestrator(lambda *a: None, transcribe_fn)
        dl = orch.enqueue_download("done")
        first = orch.enqueue_transcription("a")
        orch.enqueue_transcription("b")
        orch.enqueue_transcription("c")
        self.assertTrue(_wait_until(lambda: orch.get_job(dl).state == JobState.DONE
                                    and orch.get_job(first).state == JobState.RUNNING))

        page = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace('<script src="renderer.js"></script>',
                            '<script src="renderer.js"></script>' + DRIVER)
        try:
            dom = _serve_and_dump(orch, page, lambda p: None)
        finally:
            hold.set()
            orch.wait_idle()
            orch.shutdown()

        # dataset 用單字全小寫的名稱(駝峰會變成 data-a-b,不好抓)
        got = dict(re.findall(r'data-([a-zA-Z]+)="([^"]*)"', dom.split("<body", 1)[1].split(">", 1)[0]))
        self.assertEqual(got.get("finished"), "yes", got)
        self.assertEqual(got["total"], "3")          # 已完成的沒有勾選框
        self.assertEqual(got["selectall"], "3")
        self.assertEqual(got["cancelenabled"], "true")
        self.assertEqual(got["afterpoll"], "3")
        self.assertEqual(got["headafterpoll"], "true")
        self.assertEqual(got["selectnone"], "0")
        self.assertEqual(got["headafterone"], "false")


if __name__ == "__main__":
    unittest.main()
