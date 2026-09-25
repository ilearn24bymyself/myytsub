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
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")):
            fake_download_media.return_value = [
                {"path": "C:/fake/20260924/downloads/影片一.mp4", "title": "影片一", "channel": "頻道A",
                 "url": "https://youtube.com/watch?v=1", "upload_date": "2026-09-20", "video_id": "1"},
                {"path": "C:/fake/20260924/downloads/影片二.mp4", "title": "影片二", "channel": "頻道B",
                 "url": "https://youtube.com/watch?v=2", "upload_date": "2026-09-21", "video_id": "2"},
            ]
            download_fn = server.make_real_download_fn(fake_orchestrator)
            payload = {"url": "https://youtube.com/watch?v=x", "format_type": "video",
                       "want_srt": False, "skip_existing": False}
            download_fn(payload, mock.Mock(), mock.Mock(), mock.Mock())

        fake_orchestrator.enqueue_transcription.assert_has_calls([
            mock.call({"path": "C:/fake/20260924/downloads/影片一.mp4", "want_srt": False, "skip_existing": False,
                       "metadata": fake_download_media.return_value[0]}),
            mock.call({"path": "C:/fake/20260924/downloads/影片二.mp4", "want_srt": False, "skip_existing": False,
                       "metadata": fake_download_media.return_value[1]}),
        ])
        # format_type 要真的傳給 download_media,不是永遠寫死 audio
        self.assertEqual(fake_download_media.call_args.kwargs["format_type"], "video")
        # 下載完成要把 metadata 存進記錄,之後使用者手動挑同一份檔案轉錄才查得回出處
        fake_record.save.assert_called_once_with(fake_download_media.return_value)


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


class TranscriptionMetadataProvenanceTest(unittest.TestCase):
    """使用者實測發現:轉錄出來的 .txt/.srt「來源」欄位永遠是「本機上傳」,
    即使是本工具剛下載完自動接轉錄的檔案也一樣——因為 metadata 在下載完成
    後就被丟掉了,從沒傳進 save_transcript/save_srt。這裡驗證兩條補回路徑:
    (1) 自動接鏈時 payload 本來就帶 metadata,直接用;
    (2) 使用者手動挑本機檔案轉錄、payload 沒有 metadata 時,退回查
    download_record 記錄檔(下載過這份檔案的話查得到)。"""

    def test_metadata_from_payload_is_passed_to_save_transcript_and_save_srt(self):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("完整結果", [{"start": 0, "end": 1, "text": "完整結果"}])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False
        metadata = {"title": "影片一", "channel": "頻道A", "url": "https://youtube.com/watch?v=1",
                    "upload_date": "2026-09-20"}

        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")), \
             mock.patch.object(server, "download_record") as fake_record:
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": True, "skip_existing": False,
                           "metadata": metadata},
                          not_cancelled, mock.Mock(), mock.Mock())

        fake_transcriber.save_transcript.assert_called_once_with("完整結果", mock.ANY, metadata=metadata)
        fake_transcriber.save_srt.assert_called_once_with(mock.ANY, mock.ANY, metadata=metadata)
        # payload 已經帶 metadata,不必再去查記錄檔
        fake_record.lookup.assert_not_called()

    def test_missing_metadata_falls_back_to_download_record_lookup(self):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("完整結果", [{"start": 0, "end": 1, "text": "完整結果"}])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False
        recorded_metadata = {"title": "舊影片", "channel": "頻道C", "url": "https://youtube.com/watch?v=3",
                              "upload_date": "2026-09-01"}

        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")), \
             mock.patch.object(server, "download_record") as fake_record:
            fake_record.lookup.return_value = recorded_metadata
            transcribe_fn = server.make_real_transcribe_fn()
            # payload 沒有 metadata 這個鍵,模擬使用者手動挑本機檔案
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": False},
                          not_cancelled, mock.Mock(), mock.Mock())

        fake_record.lookup.assert_called_once_with("C:/fake/video.mp4")
        fake_transcriber.save_transcript.assert_called_once_with("完整結果", mock.ANY, metadata=recorded_metadata)

    def test_falls_back_to_source_lookup_when_record_also_has_nothing(self):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("完整結果", [{"start": 0, "end": 1, "text": "完整結果"}])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False
        guessed_metadata = {"title": "反查到的影片", "channel": "某頻道",
                             "url": "https://youtube.com/watch?v=guessed", "upload_date": None}

        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "source_lookup") as fake_lookup:
            fake_record.lookup.return_value = None
            fake_lookup.guess_metadata.return_value = guessed_metadata
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": False},
                          not_cancelled, mock.Mock(), mock.Mock())

        fake_lookup.guess_metadata.assert_called_once_with("C:/fake/video.mp4")
        fake_transcriber.save_transcript.assert_called_once_with("完整結果", mock.ANY, metadata=guessed_metadata)


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
