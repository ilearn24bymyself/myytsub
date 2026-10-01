import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from test_ui_source_lookup import _mock_orchestrator, _serve_and_dump  # noqa: E402
from test_ui_status_display import CHROME  # noqa: E402

DRIVER = """<script>
document.getElementById("download-only").checked = %s;
document.getElementById("skip-transcribed").checked = %s;
document.getElementById("yt-url").value = "https://www.youtube.com/@channel/videos";
document.getElementById("add-url").click();
document.getElementById("start-btn").click();
</script>"""


def _page(download_only, skip_transcribed):
    page = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    driver = DRIVER % (str(download_only).lower(), str(skip_transcribed).lower())
    return page.replace('<script src="renderer.js"></script>', '<script src="renderer.js"></script>' + driver)


@unittest.skipUnless(CHROME, "找不到 Chrome,略過畫面測試")
class DownloadOptionsInPageTest(unittest.TestCase):
    """畫面上多了兩個選項:「只下載,不轉錄」「略過已有逐字稿的影片」,按開始處理時要連同網址一起送出。"""

    def _submitted(self, download_only, skip_transcribed):
        orch = _mock_orchestrator()
        orch.enqueue_download.return_value = "1"
        _serve_and_dump(orch, _page(download_only, skip_transcribed), lambda p: None)
        self.assertEqual(orch.enqueue_download.call_count, 1)
        return orch.enqueue_download.call_args.args[0]

    def test_the_checked_options_are_sent_with_the_url(self):
        payload = self._submitted(download_only=True, skip_transcribed=False)
        self.assertEqual(payload["url"], "https://www.youtube.com/@channel/videos")
        self.assertIs(payload["download_only"], True)
        self.assertIs(payload["skip_transcribed"], False)

    def test_the_opposite_choices_are_sent_too(self):
        payload = self._submitted(download_only=False, skip_transcribed=True)
        self.assertIs(payload["download_only"], False)
        self.assertIs(payload["skip_transcribed"], True)


if __name__ == "__main__":
    unittest.main()
