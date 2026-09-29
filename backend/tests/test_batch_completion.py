import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from batch_completion import BatchCompletion  # noqa: E402
from orchestrator import Orchestrator, JobState, JobType, RateLimited  # noqa: E402

DL = JobType.DOWNLOAD
TX = JobType.TRANSCRIBE


def _make(download_fn, transcribe_fn):
    """真的 orchestrator + BatchCompletion;opened 記錄每次「一批跑完」回報的工作種類。"""
    opened = []
    holder = {}
    batch = BatchCompletion(
        is_settled=lambda: holder["orch"].is_settled(),
        on_complete=lambda types: opened.append(set(types)),
    )
    orch = Orchestrator(download_fn, transcribe_fn, on_job_terminal=batch.on_job_terminal)
    holder["orch"] = orch
    return orch, opened


def _ok(payload, cancel_event, pause_event, report_progress):
    return None


class BatchCompletionTest(unittest.TestCase):
    """舊版 3-4 每批做完會自動打開對應資料夾,改寫時漏掉了。「一批跑完」= 下載和轉錄兩條佇列
    都空了、而且上次開過之後至少有一件成功;開哪種資料夾看這批實際成功的是哪種工作。"""

    def test_a_local_file_transcription_reports_only_transcribe(self):
        orch, opened = _make(_ok, _ok)
        orch.enqueue_transcription("C:/video.mp4")
        orch.wait_idle()
        self.assertEqual(opened, [{TX}])
        orch.shutdown()

    def test_download_that_chains_a_transcription_reports_once_after_the_transcription(self):
        holder = {}

        def download_fn(payload, cancel_event, pause_event, report_progress):
            holder["orch"].enqueue_transcription("C:/downloaded.mp4")  # 跟真的下載一樣,結束前先排好轉錄

        orch, opened = _make(download_fn, _ok)
        holder["orch"] = orch
        orch.enqueue_download("https://youtube.com/watch?v=x")
        orch.wait_idle()
        self.assertEqual(opened, [{DL, TX}])
        orch.shutdown()

    def test_does_not_report_while_the_other_lane_is_still_running(self):
        release = threading.Event()
        started = threading.Event()

        def slow_transcribe(payload, cancel_event, pause_event, report_progress):
            started.set()
            release.wait(10)

        orch, opened = _make(_ok, slow_transcribe)
        orch.enqueue_transcription("C:/long.mp4")
        self.assertTrue(started.wait(5))
        orch.enqueue_download("https://youtube.com/watch?v=x")
        # 下載那條先做完了,但轉錄還在跑:不能就宣告一批跑完
        deadline = threading.Event()
        deadline.wait(0.5)
        self.assertEqual(opened, [])
        release.set()
        orch.wait_idle()
        self.assertEqual(opened, [{DL, TX}])
        orch.shutdown()

    def test_nothing_succeeded_means_nothing_is_reported(self):
        def failing(payload, cancel_event, pause_event, report_progress):
            raise Exception("boom")

        orch, opened = _make(failing, failing)
        orch.enqueue_download("u")
        orch.enqueue_transcription("p")
        orch.wait_idle()
        self.assertEqual(opened, [])
        orch.shutdown()


class BatchCompletionWithRateLimitTest(unittest.TestCase):
    """YouTube 限流:撞到那支和後面排著的都變「待重試」(不是在跑),前面成功的照常做完。"""

    def _rate_limited_setup(self):
        limited = {"on": True}
        holder = {}

        def download_fn(payload, cancel_event, pause_event, report_progress):
            if payload == "hits-limit" and limited["on"]:
                raise RateLimited("429")
            holder["orch"].enqueue_transcription(f"C:/{payload}.mp4")

        orch, opened = _make(download_fn, _ok)
        holder["orch"] = orch
        ids = [orch.enqueue_download(u) for u in ("ok-1", "ok-2", "hits-limit", "never-tried")]
        orch.wait_idle()
        return orch, opened, limited, ids

    def test_partial_results_are_reported_even_though_some_are_waiting_for_retry(self):
        orch, opened, _limited, ids = self._rate_limited_setup()
        self.assertEqual(orch.get_job(ids[2]).state, JobState.PENDING_RETRY)
        self.assertEqual(orch.get_job(ids[3]).state, JobState.PENDING_RETRY)
        # 已經拿到的成果馬上讓使用者看得到,不必等被限流的那批
        self.assertEqual(opened, [{DL, TX}])
        orch.shutdown()

    def test_retry_that_is_limited_again_with_nothing_new_does_not_reopen(self):
        orch, opened, _limited, _ids = self._rate_limited_setup()
        orch.retry_pending()
        orch.wait_idle()
        self.assertEqual(len(opened), 1)
        orch.shutdown()

    def test_retry_that_succeeds_reports_again(self):
        orch, opened, limited, ids = self._rate_limited_setup()
        limited["on"] = False
        orch.retry_pending()
        orch.wait_idle()
        self.assertEqual(orch.get_job(ids[2]).state, JobState.DONE)
        self.assertEqual(orch.get_job(ids[3]).state, JobState.DONE)
        self.assertEqual(opened, [{DL, TX}, {DL, TX}])
        orch.shutdown()


if __name__ == "__main__":
    unittest.main()
