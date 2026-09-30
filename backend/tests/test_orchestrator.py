import sys
import time
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator import Orchestrator, JobState, RateLimited  # noqa: E402


class OrchestratorTranscriptionTest(unittest.TestCase):
    def test_single_transcription_job_runs_to_done_and_fires_terminal_callback(self):
        terminal_calls = []
        orch = Orchestrator(
            download_fn=lambda payload, cancel_event, pause_event, report_progress: None,
            transcribe_fn=lambda payload, cancel_event, pause_event, report_progress: None,
            on_job_terminal=lambda job: terminal_calls.append((job.id, job.state)),
        )
        job_id = orch.enqueue_transcription("C:/video.mp4")
        orch.wait_idle()

        job = orch.get_job(job_id)
        self.assertEqual(job.state, JobState.DONE)
        self.assertEqual(terminal_calls, [(job_id, JobState.DONE)])
        orch.shutdown()


class OrchestratorDownloadTest(unittest.TestCase):
    def test_single_download_job_runs_to_done_and_fires_terminal_callback(self):
        terminal_calls = []
        orch = Orchestrator(
            download_fn=lambda payload, cancel_event, pause_event, report_progress: None,
            transcribe_fn=lambda payload, cancel_event, pause_event, report_progress: None,
            on_job_terminal=lambda job: terminal_calls.append((job.id, job.state)),
        )
        job_id = orch.enqueue_download("https://youtube.com/watch?v=x")
        orch.wait_idle()

        job = orch.get_job(job_id)
        self.assertEqual(job.state, JobState.DONE)
        self.assertEqual(terminal_calls, [(job_id, JobState.DONE)])
        orch.shutdown()

    def test_multiple_downloads_run_strictly_sequentially(self):
        order = []
        release = {1: threading.Event(), 2: threading.Event(), 3: threading.Event()}

        def fake_download(payload, cancel_event, pause_event, report_progress):
            order.append(("start", payload))
            release[payload].wait(timeout=2)
            order.append(("end", payload))

        orch = Orchestrator(download_fn=fake_download, transcribe_fn=lambda p, c, pe, rp: None)
        orch.enqueue_download(1)
        orch.enqueue_download(2)
        orch.enqueue_download(3)

        # 故意不放行任何一項,等一下確認只有第一項開始執行(其餘還在排隊)
        time.sleep(0.1)
        self.assertEqual(order, [("start", 1)])

        release[1].set()
        time.sleep(0.1)
        self.assertEqual(order, [("start", 1), ("end", 1), ("start", 2)])

        release[2].set()
        release[3].set()
        orch.wait_idle()
        self.assertEqual(
            order,
            [("start", 1), ("end", 1), ("start", 2), ("end", 2), ("start", 3), ("end", 3)],
        )
        orch.shutdown()


class OrchestratorConcurrentLanesTest(unittest.TestCase):
    def test_transcription_lane_not_blocked_by_stuck_download_lane(self):
        download_release = threading.Event()

        def stuck_download(payload, cancel_event, pause_event, report_progress):
            download_release.wait(timeout=2)

        transcribe_done = threading.Event()

        def quick_transcribe(payload, cancel_event, pause_event, report_progress):
            transcribe_done.set()

        orch = Orchestrator(download_fn=stuck_download, transcribe_fn=quick_transcribe)
        orch.enqueue_download("https://youtube.com/watch?v=stuck")
        orch.enqueue_transcription("C:/video.mp4")

        # 下載線卡住的情況下,轉錄線要能在很短時間內完成,不等下載線
        self.assertTrue(transcribe_done.wait(timeout=1), "轉錄線被下載線卡住了")

        download_release.set()
        orch.wait_idle()
        orch.shutdown()


class OrchestratorErrorIsolationTest(unittest.TestCase):
    def test_one_failed_download_does_not_abort_the_rest_of_the_batch(self):
        def flaky_download(payload, cancel_event, pause_event, report_progress):
            if payload == 2:
                raise RuntimeError("網路中斷")

        orch = Orchestrator(download_fn=flaky_download, transcribe_fn=lambda p, c, pe, rp: None)
        ids = [orch.enqueue_download(n) for n in (1, 2, 3)]
        orch.wait_idle()

        states = [orch.get_job(i).state for i in ids]
        self.assertEqual(states, [JobState.DONE, JobState.ERROR, JobState.DONE])
        self.assertEqual(orch.get_job(ids[1]).error_message, "網路中斷")
        orch.shutdown()


class OrchestratorRateLimitTest(unittest.TestCase):
    def test_rate_limit_signal_skips_remaining_queued_downloads_as_pending_retry(self):
        attempted = []

        def rate_limited_after_first(payload, cancel_event, pause_event, report_progress):
            attempted.append(payload)
            if payload == 1:
                raise RateLimited("YouTube 429 / bot-check")

        orch = Orchestrator(download_fn=rate_limited_after_first, transcribe_fn=lambda p, c, pe, rp: None)
        ids = [orch.enqueue_download(n) for n in (1, 2, 3)]
        orch.wait_idle()

        states = [orch.get_job(i).state for i in ids]
        self.assertEqual(states, [JobState.PENDING_RETRY, JobState.PENDING_RETRY, JobState.PENDING_RETRY])
        # 第 2、3 項不該被嘗試過 —— 是直接跳過標記,不是嘗試後失敗
        self.assertEqual(attempted, [1])
        orch.shutdown()

    def test_already_done_downloads_before_the_rate_limited_one_keep_their_state(self):
        def rate_limited_on_third(payload, cancel_event, pause_event, report_progress):
            if payload == 3:
                raise RateLimited("YouTube 429 / bot-check")

        orch = Orchestrator(download_fn=rate_limited_on_third, transcribe_fn=lambda p, c, pe, rp: None)
        ids = [orch.enqueue_download(n) for n in (1, 2, 3, 4)]
        orch.wait_idle()

        states = [orch.get_job(i).state for i in ids]
        self.assertEqual(
            states,
            [JobState.DONE, JobState.DONE, JobState.PENDING_RETRY, JobState.PENDING_RETRY],
        )
        orch.shutdown()


class OrchestratorRetryTest(unittest.TestCase):
    def test_retry_pending_reenqueues_and_processes_normally(self):
        calls = []
        succeed_now = threading.Event()

        def download(payload, cancel_event, pause_event, report_progress):
            calls.append(payload)
            if not succeed_now.is_set():
                raise RateLimited("YouTube 429 / bot-check")

        orch = Orchestrator(download_fn=download, transcribe_fn=lambda p, c, pe, rp: None)
        ids = [orch.enqueue_download(n) for n in (1, 2)]
        orch.wait_idle()
        self.assertEqual([orch.get_job(i).state for i in ids], [JobState.PENDING_RETRY, JobState.PENDING_RETRY])

        # 使用者相信限流解除了,觸發重試;這次讓下載函式成功
        succeed_now.set()
        orch.retry_pending()
        orch.wait_idle()

        self.assertEqual([orch.get_job(i).state for i in ids], [JobState.DONE, JobState.DONE])
        # 項目 1 觸發限流,被嘗試兩次(第一次限流、重試後成功)
        # 項目 2 在第一輪被跳過、從未嘗試過(票 07 行為),重試後才第一次被嘗試
        self.assertEqual(calls.count(1), 2)
        self.assertEqual(calls.count(2), 1)
        orch.shutdown()

    def test_untouched_pending_retry_jobs_are_never_retried_automatically(self):
        calls = []

        def always_rate_limited(payload, cancel_event, pause_event, report_progress):
            calls.append(payload)
            raise RateLimited("YouTube 429 / bot-check")

        orch = Orchestrator(download_fn=always_rate_limited, transcribe_fn=lambda p, c, pe, rp: None)
        orch.enqueue_download(1)
        orch.wait_idle()
        self.assertEqual(calls, [1])

        time.sleep(0.2)  # 沒有觸發 retry_pending,不該有任何自動重試
        self.assertEqual(calls, [1])
        orch.shutdown()


class OrchestratorCancelTest(unittest.TestCase):
    def test_cancel_running_job_stops_it_without_affecting_the_queue(self):
        started = threading.Event()

        def cancellable_download(payload, cancel_event, pause_event, report_progress):
            started.set()
            cancel_event.wait(timeout=2)  # 模擬會定期檢查取消旗標的長時間下載

        orch = Orchestrator(download_fn=cancellable_download, transcribe_fn=lambda p, c, pe, rp: None)
        id1 = orch.enqueue_download("slow")
        id2 = orch.enqueue_download("next")

        self.assertTrue(started.wait(timeout=1))
        orch.cancel(id1)
        orch.wait_idle()

        self.assertEqual(orch.get_job(id1).state, JobState.CANCELLED)
        self.assertEqual(orch.get_job(id2).state, JobState.DONE)
        orch.shutdown()


class OrchestratorProgressTest(unittest.TestCase):
    def test_report_progress_updates_job_progress_and_message_while_running(self):
        reached_50 = threading.Event()
        release = threading.Event()

        def transcribing(payload, cancel_event, pause_event, report_progress):
            report_progress(50.0, "轉錄中")
            reached_50.set()
            release.wait(timeout=2)
            report_progress(100.0, "完成")

        orch = Orchestrator(download_fn=lambda p, c, pe, rp: None, transcribe_fn=transcribing)
        job_id = orch.enqueue_transcription("C:/video.mp4")

        self.assertTrue(reached_50.wait(timeout=1))
        job = orch.get_job(job_id)
        self.assertEqual(job.progress, 50.0)
        self.assertEqual(job.message, "轉錄中")

        release.set()
        orch.wait_idle()
        self.assertEqual(orch.get_job(job_id).progress, 100.0)
        orch.shutdown()

    def test_new_job_starts_at_zero_progress(self):
        orch = Orchestrator(download_fn=lambda p, c, pe, rp: None, transcribe_fn=lambda p, c, pe, rp: None)
        job_id = orch.enqueue_transcription("C:/video.mp4")
        self.assertEqual(orch.get_job(job_id).progress, 0.0)
        orch.wait_idle()
        orch.shutdown()


class OrchestratorPauseTest(unittest.TestCase):
    def test_pause_blocks_transcription_until_resumed(self):
        # ready_to_check_pause 讓測試完全掌控 fn「什麼時候真的去檢查 pause_event」,
        # 避免跟背景執行緒的排程時機賽跑(先 pause() 再放行,保證 pause_event
        # 一定是在 fn 檢查它之前就已經清掉了)。
        ready_to_check_pause = threading.Event()
        finished = threading.Event()

        def transcribing(payload, cancel_event, pause_event, report_progress):
            ready_to_check_pause.wait(timeout=2)
            pause_event.wait(timeout=2)
            finished.set()

        orch = Orchestrator(download_fn=lambda p, c, pe, rp: None, transcribe_fn=transcribing)
        job_id = orch.enqueue_transcription("C:/video.mp4")

        orch.pause(job_id)
        self.assertTrue(orch.get_job(job_id).paused)
        ready_to_check_pause.set()
        time.sleep(0.2)
        self.assertFalse(finished.is_set(), "暫停沒有生效,工作已經跑完了")

        orch.resume(job_id)
        self.assertFalse(orch.get_job(job_id).paused)
        self.assertTrue(finished.wait(timeout=1))
        orch.wait_idle()
        orch.shutdown()

    def test_cancelling_a_paused_job_wakes_it_up_so_it_can_stop(self):
        # 真實轉錄迴圈是「先 pause_event.wait()(沒有逾時)、再檢查 cancel_event」:
        # 暫停中按取消,如果取消不順便解除暫停,迴圈永遠卡在 wait(),取消沒反應、工作也不會結束
        at_wait = threading.Event()

        def transcribing(payload, cancel_event, pause_event, report_progress):
            at_wait.set()
            pause_event.wait()
            if cancel_event.is_set():
                return

        orch = Orchestrator(download_fn=lambda p, c, pe, rp: None, transcribe_fn=transcribing)
        job_id = orch.enqueue_transcription("C:/video.mp4")
        orch.pause(job_id)
        self.assertTrue(at_wait.wait(timeout=2))

        orch.cancel(job_id)
        deadline = time.time() + 2
        while orch.get_job(job_id).state == JobState.RUNNING and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(orch.get_job(job_id).state, JobState.CANCELLED)
        self.assertFalse(orch.get_job(job_id).paused)
        orch.shutdown()

    def test_paused_flag_resets_when_a_paused_job_reaches_a_terminal_state(self):
        # 一個工作被暫停後又結束(這裡讓它出錯結束),paused 不該卡在 True,
        # 不然之後任何讀 job.paused 的地方都會被誤導。
        def erroring(payload, cancel_event, pause_event, report_progress):
            raise RuntimeError("轉錄失敗")

        orch = Orchestrator(download_fn=lambda p, c, pe, rp: None, transcribe_fn=erroring)
        job_id = orch.enqueue_transcription("C:/video.mp4")
        orch.pause(job_id)  # 暫停一個「即將」執行的工作,pause_event 先被清掉
        orch.wait_idle()

        job = orch.get_job(job_id)
        self.assertEqual(job.state, JobState.ERROR)
        self.assertFalse(job.paused, "工作已經結束,paused 不該還停在 True")
        orch.shutdown()

    def test_pausing_a_download_job_has_no_effect_downloads_dont_support_it(self):
        # 下載的實作根本不會去看 pause_event,所以呼叫 pause() 不該讓它卡住
        finished = threading.Event()

        def downloading(payload, cancel_event, pause_event, report_progress):
            finished.set()  # 完全不理會 pause_event,模擬下載目前的真實行為

        orch = Orchestrator(download_fn=downloading, transcribe_fn=lambda p, c, pe, rp: None)
        job_id = orch.enqueue_download("https://youtube.com/watch?v=x")
        orch.pause(job_id)  # 呼叫了,但下載的假函式沒有去檢查,不會有效果

        self.assertTrue(finished.wait(timeout=1))
        orch.wait_idle()
        self.assertEqual(orch.get_job(job_id).state, JobState.DONE)
        orch.shutdown()


class OrchestratorListJobsTest(unittest.TestCase):
    def test_list_jobs_returns_every_job_in_enqueue_order(self):
        orch = Orchestrator(download_fn=lambda p, c, pe, rp: None, transcribe_fn=lambda p, c, pe, rp: None)
        id1 = orch.enqueue_download("a")
        id2 = orch.enqueue_transcription("b")
        orch.wait_idle()

        ids = [j.id for j in orch.list_jobs()]
        self.assertEqual(ids, [id1, id2])
        orch.shutdown()


if __name__ == "__main__":
    unittest.main()
