import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class DownloadChainsToTranscriptionTest(unittest.TestCase):
    """票 03 的驗收條件:下載完成後 index 要能反映這筆項目。build_day_index() 只掃
    transcripts/*.txt,不看 downloads/,所以下載完成後必須自動接一個轉錄工作,
    index 才有東西可以反映(這也是 spec.md 裡「transcription lane 含
    post-download transcription jobs」這句話的具體實作)。"""

    def test_completed_download_enqueues_transcription_for_each_downloaded_file(self):
        fake_orchestrator = mock.Mock()

        with mock.patch.object(server, "download_media") as fake_download_media, \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")):
            fake_download_media.return_value = [
                {"path": "C:/fake/20260924/downloads/影片一.mp4"},
                {"path": "C:/fake/20260924/downloads/影片二.mp4"},
            ]
            download_fn = server.make_real_download_fn(fake_orchestrator)
            payload = {"url": "https://youtube.com/watch?v=x", "format_type": "video",
                       "want_srt": False, "skip_existing": False}
            download_fn(payload, mock.Mock(), mock.Mock(), mock.Mock())

        fake_orchestrator.enqueue_transcription.assert_has_calls([
            mock.call({"path": "C:/fake/20260924/downloads/影片一.mp4", "want_srt": False, "skip_existing": False}),
            mock.call({"path": "C:/fake/20260924/downloads/影片二.mp4", "want_srt": False, "skip_existing": False}),
        ])
        # format_type 要真的傳給 download_media,不是永遠寫死 audio
        self.assertEqual(fake_download_media.call_args.kwargs["format_type"], "video")


class CancelledTranscriptionDiscardsPartialOutputTest(unittest.TestCase):
    """使用者實測抓到的 bug:轉錄中途取消,系統仍然把「取消當下已經算出來的
    部分結果」存成 .txt/.srt,檔名、位置都跟正常完成的檔案一模一樣,完全看不出
    是不完整的殘留。取消就該丟棄結果,不寫任何檔案。"""

    def test_no_files_written_when_transcription_is_cancelled_midway(self):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("部分結果", [{"start": 0, "end": 1, "text": "部分結果"}])
        already_cancelled = mock.Mock()
        already_cancelled.is_set.return_value = True  # 模擬 transcribe() 因為被取消而提早結束

        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")):
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": True, "skip_existing": False},
                          already_cancelled, mock.Mock(), mock.Mock())

        fake_transcriber.save_transcript.assert_not_called()
        fake_transcriber.save_srt.assert_not_called()

    def test_files_still_written_normally_when_not_cancelled(self):
        # 對照組:確保上面那個修正沒有連正常完成的情況也一起擋掉
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("完整結果", [{"start": 0, "end": 1, "text": "完整結果"}])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False

        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")):
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": True, "skip_existing": False},
                          not_cancelled, mock.Mock(), mock.Mock())

        fake_transcriber.save_transcript.assert_called_once()
        fake_transcriber.save_srt.assert_called_once()


class PauseResumeHttpRoutesTest(unittest.TestCase):
    """/api/jobs/<id>/pause、/resume 只有 orchestrator 層的邏輯測試,HTTP 路由本身沒測過
    (code review 抓到的小缺口)。這裡只測「路由有沒有把 id 正確解析出來、呼叫到
    orchestrator 上對的方法」,用假 orchestrator 隔開真的背景工作處理速度
    (真的轉錄工作跑多快是計時不穩定因素,不是這個測試要驗證的東西)。"""

    def test_pause_and_resume_routes_call_the_matching_orchestrator_method_with_the_job_id(self):
        from http.server import ThreadingHTTPServer

        fake_orchestrator = mock.Mock()
        handler = server.make_handler(fake_orchestrator)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            for action, method_name in server._JOB_ACTIONS.items():
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/jobs/job-42/{action}", data=b"{}", method="POST")
                urllib.request.urlopen(req)
                getattr(fake_orchestrator, method_name).assert_called_once_with("job-42")
        finally:
            srv.shutdown()
            srv.server_close()  # 釋放監聽中的 socket,避免測試留下 ResourceWarning


if __name__ == "__main__":
    unittest.main()
