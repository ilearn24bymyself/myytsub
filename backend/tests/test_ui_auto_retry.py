import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from orchestrator import Job, JobState, JobType  # noqa: E402
from test_ui_source_lookup import _mock_orchestrator, _serve_and_dump  # noqa: E402
from test_ui_status_display import CHROME  # noqa: E402


def _dump(auto_retry_at):
    job = Job("1", JobType.DOWNLOAD, {"url": "https://www.youtube.com/@channel/videos"})
    job.state = JobState.PENDING_RETRY
    job.message = "被 YouTube 限流"
    orch = _mock_orchestrator()
    orch.list_jobs.return_value = [job]
    orch.auto_retry_at = auto_retry_at
    page = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    dom = _serve_and_dump(orch, page, lambda p: None)
    rows = re.findall(r"<tr>(.*?)</tr>", dom.split("<tbody>")[1], re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", rows[0])), rows[0]


@unittest.skipUnless(CHROME, "找不到 Chrome,略過畫面測試")
class AutoRetryDisplayTest(unittest.TestCase):
    def test_a_waiting_job_shows_when_it_will_retry_and_can_be_selected_for_cancelling(self):
        text, row_html = _dump(1790000000.5)
        self.assertIsNotNone(re.search(r"將於 \d{2}/\d{2} \d{2}:\d{2} 自動重試", text), text)
        self.assertIn("data-select", row_html)   # 有勾選框:想放棄這項就勾起來按「取消勾選項目」

    def test_without_a_schedule_it_does_not_claim_one(self):
        text, _row = _dump(None)
        self.assertNotIn("自動重試", text)


if __name__ == "__main__":
    unittest.main()
