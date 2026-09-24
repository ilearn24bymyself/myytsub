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
