import contextlib
import io
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator import Orchestrator, JobState, RateLimited  # noqa: E402


def _noop(payload, cancel_event, pause_event, report_progress):
    return None


def _run_one(download_fn=_noop, transcribe_fn=_noop, kind="download"):
    orch = Orchestrator(download_fn=download_fn, transcribe_fn=transcribe_fn)
    job_id = orch.enqueue_download("u") if kind == "download" else orch.enqueue_transcription("p")
    orch.wait_idle()
    job = orch.get_job(job_id)
    orch.shutdown()
    return job


class StatusMessageAfterFinishTest(unittest.TestCase):
    """使用者實測畫面:工作已經「完成」,狀態底下卻還寫著「下載中」「轉錄中」——
    執行中的進度文字在工作結束後沒被換掉,讓人以為還沒做完。"""

    def test_in_progress_message_does_not_survive_a_finished_job(self):
        def fn(payload, cancel_event, pause_event, report_progress):
            report_progress(50.0, "下載中")
        job = _run_one(download_fn=fn)
        self.assertEqual(job.state, JobState.DONE)
        self.assertIsNone(job.message)

    def test_a_final_message_set_by_the_job_is_kept(self):
        def fn(payload, cancel_event, pause_event, report_progress):
            report_progress(50.0, "轉錄中")
            report_progress(100.0, "轉錄完成", final=True)
        job = _run_one(transcribe_fn=fn, kind="transcribe")
        self.assertEqual(job.state, JobState.DONE)
        self.assertEqual(job.message, "轉錄完成")

    def test_in_progress_message_is_cleared_when_the_job_fails(self):
        def fn(payload, cancel_event, pause_event, report_progress):
            report_progress(10.0, "轉錄中")
            raise Exception("boom")
        job = _run_one(transcribe_fn=fn, kind="transcribe")
        self.assertEqual(job.state, JobState.ERROR)
        self.assertEqual(job.error_message, "boom")
        self.assertIsNone(job.message)


class RateLimitExplanationTest(unittest.TestCase):
    """使用者只看到「待重試」三個字,不知道為什麼。"""

    def test_rate_limited_and_skipped_jobs_say_why_they_are_waiting(self):
        calls = []

        def download_fn(payload, cancel_event, pause_event, report_progress):
            calls.append(payload)
            if payload == "hits-limit":
                raise RateLimited("429")

        orch = Orchestrator(download_fn=download_fn, transcribe_fn=_noop)
        hit = orch.enqueue_download("hits-limit")
        skipped = orch.enqueue_download("never-tried")
        orch.wait_idle()

        self.assertEqual(orch.get_job(hit).state, JobState.PENDING_RETRY)
        self.assertEqual(orch.get_job(skipped).state, JobState.PENDING_RETRY)
        self.assertEqual(calls, ["hits-limit"])
        self.assertIn("限流", orch.get_job(hit).message)
        self.assertIn("限流", orch.get_job(skipped).message)
        orch.shutdown()


class StartedAtTest(unittest.TestCase):
    """畫面要顯示「已執行 mm:ss」,才看得出轉錄是不是還在跑(Whisper 進度是一批一批跳的)。"""

    def test_started_at_is_none_until_the_job_actually_starts_running(self):
        release = threading.Event()
        running = threading.Event()

        def blocking_fn(payload, cancel_event, pause_event, report_progress):
            running.set()
            release.wait(5)

        orch = Orchestrator(download_fn=blocking_fn, transcribe_fn=_noop)
        first = orch.enqueue_download("first")
        second = orch.enqueue_download("second")
        self.assertTrue(running.wait(5))

        before = time.time()
        self.assertIsNotNone(orch.get_job(first).started_at)
        self.assertLessEqual(orch.get_job(first).started_at, before)
        self.assertIsNone(orch.get_job(second).started_at)

        release.set()
        orch.wait_idle()
        orch.shutdown()


class RetryStartsWithAFreshMessageTest(unittest.TestCase):
    """被限流的工作按「重試」重跑時,不能沿用上一輪的說明文字:
    重跑失敗卻還寫著「被 YouTube 限流…」是錯的。"""

    def test_a_retried_job_does_not_show_the_previous_runs_explanation(self):
        attempts = []
        seen_while_running = []
        holder = {}

        seen_progress = []

        def download_fn(payload, cancel_event, pause_event, report_progress):
            attempts.append(1)
            job = holder["orch"].get_job(holder["job_id"])
            seen_while_running.append(job.message)
            seen_progress.append(job.progress)
            if len(attempts) == 1:
                report_progress(40.0, "下載中")
                raise RateLimited("429")
            raise Exception("boom")

        orch = Orchestrator(download_fn=download_fn, transcribe_fn=_noop)
        holder["orch"] = orch
        holder["job_id"] = orch.enqueue_download("u")
        orch.wait_idle()
        self.assertEqual(orch.get_job(holder["job_id"]).state, JobState.PENDING_RETRY)

        orch.retry_pending()
        orch.wait_idle()
        job = orch.get_job(holder["job_id"])
        self.assertEqual(job.state, JobState.ERROR)
        self.assertIsNone(job.message)                # 結束後不殘留上一輪的限流說明
        self.assertIsNone(seen_while_running[1])      # 重跑的過程中也不顯示舊文字
        self.assertEqual(seen_progress[1], 0.0)       # 百分比也從頭算,不是先顯示上一輪的 40%
        orch.shutdown()

    def test_a_job_waiting_in_the_queue_after_retry_does_not_show_the_old_explanation(self):
        release = threading.Event()
        first_retry_running = threading.Event()
        state = {"retrying": False}

        def download_fn(payload, cancel_event, pause_event, report_progress):
            if payload == "hits-limit" and not state["retrying"]:
                raise RateLimited("429")
            if state["retrying"] and payload == "hits-limit":
                first_retry_running.set()
                release.wait(5)            # 讓這一支卡住,後面那支就會停在排隊中

        orch = Orchestrator(download_fn=download_fn, transcribe_fn=_noop)
        orch.enqueue_download("hits-limit")
        queued = orch.enqueue_download("skipped")
        orch.wait_idle()
        self.assertIn("限流", orch.get_job(queued).message)

        state["retrying"] = True
        orch.retry_pending()
        self.assertTrue(first_retry_running.wait(5))
        job = orch.get_job(queued)
        self.assertEqual(job.state, JobState.PENDING)
        self.assertIsNone(job.message)               # 排隊中不該還寫著上一輪的限流說明
        release.set()
        orch.wait_idle()
        orch.shutdown()


class TerminalCallbackFailureTest(unittest.TestCase):
    """一批跑完時開資料夾、建總覽頁這類收尾動作失敗,絕對不能拖垮工作本身:
    例外穿出來會讓那條佇列的執行緒直接死掉,後面的工作永遠不會跑。"""

    def test_a_failing_terminal_callback_does_not_kill_the_lane(self):
        second_ran = threading.Event()

        def download_fn(payload, cancel_event, pause_event, report_progress):
            if payload == "second":
                second_ran.set()

        def on_terminal(job):
            raise RuntimeError("could not build the index")

        orch = Orchestrator(download_fn=download_fn, transcribe_fn=_noop, on_job_terminal=on_terminal)
        with contextlib.redirect_stderr(io.StringIO()):   # 印出的錯誤追蹤不要洗版測試輸出
            orch.enqueue_download("first")
            second = orch.enqueue_download("second")
            ran = second_ran.wait(3)
            if ran:
                orch.wait_idle()
        self.assertTrue(ran, "第一個工作的收尾回呼丟例外後,這條佇列的執行緒死了")
        self.assertEqual(orch.get_job(second).state, JobState.DONE)
        orch.shutdown()


if __name__ == "__main__":
    unittest.main()
