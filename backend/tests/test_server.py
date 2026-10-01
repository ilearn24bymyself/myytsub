import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
import ledger  # noqa: E402

_module_patches = []
_module_tmp = None


def setUpModule():
    global _module_tmp
    import tempfile
    _module_tmp = tempfile.TemporaryDirectory()
    for p in (mock.patch.object(server, "list_entries", return_value=None),   # None = 讀不到清單,走「整個網址一次下載」
              mock.patch.object(server, "BASE_DIR", Path(_module_tmp.name))):
        p.start()
        _module_patches.append(p)


def tearDownModule():
    for p in _module_patches:
        p.stop()
    _module_tmp.cleanup()


class DownloadChainsToTranscriptionTest(unittest.TestCase):
    """票 03 的驗收條件:下載完成後 index 要能反映這筆項目。build_day_index() 只掃
    transcripts/*.txt,不看 downloads/,所以下載完成後必須自動接一個轉錄工作,
    index 才有東西可以反映(這也是 spec.md 裡「transcription lane 含
    post-download transcription jobs」這句話的具體實作)。"""

    def test_completed_download_enqueues_transcription_for_each_downloaded_file(self):
        fake_orchestrator = mock.Mock()

        with mock.patch.object(server, "download_media") as fake_download_media, \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
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
            download_fn(payload, _not_cancelled(), mock.Mock(), mock.Mock())

        fake_orchestrator.enqueue_transcription.assert_has_calls([
            mock.call({"path": "C:/fake/20260924/downloads/影片一.mp4", "want_srt": False, "skip_existing": False,
                       "metadata": fake_download_media.return_value[0]}),
            mock.call({"path": "C:/fake/20260924/downloads/影片二.mp4", "want_srt": False, "skip_existing": False,
                       "metadata": fake_download_media.return_value[1]}),
        ])
        # format_type 要真的傳給 download_media,不是永遠寫死 audio
        self.assertEqual(fake_download_media.call_args.kwargs["format_type"], "video")
        # 每支影片各存一次記錄,並在影片旁寫資訊檔(影片搬家資訊也跟著走)
        entries = fake_download_media.return_value
        self.assertEqual(fake_record.save.call_args_list, [mock.call([entries[0]]), mock.call([entries[1]])])
        self.assertEqual(fake_sidecar.write.call_args_list,
                         [mock.call(entries[0]["path"], entries[0]), mock.call(entries[1]["path"], entries[1])])

    def test_already_archived_video_reports_the_previous_path_when_found_via_record(self):
        fake_orchestrator = mock.Mock()
        fake_report_progress = mock.Mock()

        with mock.patch.object(server, "download_media", return_value=[]), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260926")):
            fake_record.find_path_by_video_id.return_value = "C:/fake/20260920/downloads/舊影片.mp4"
            download_fn = server.make_real_download_fn(fake_orchestrator)
            payload = {"url": "https://www.youtube.com/watch?v=pfGg0Uris1w", "format_type": "video",
                       "want_srt": True, "skip_existing": True}
            download_fn(payload, _not_cancelled(), mock.Mock(), fake_report_progress)

        fake_record.find_path_by_video_id.assert_called_once_with("pfGg0Uris1w")
        message = fake_report_progress.call_args.args[1]
        self.assertIn("C:/fake/20260920/downloads/舊影片.mp4", message)

    def test_already_archived_video_falls_back_to_title_search_when_record_has_nothing(self):
        fake_orchestrator = mock.Mock()
        fake_report_progress = mock.Mock()

        with mock.patch.object(server, "download_media", return_value=[]), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "fetch_title_only", return_value="舊影片標題"), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260926")):
            fake_record.find_path_by_video_id.return_value = None
            fake_record.find_path_by_title.return_value = "C:/fake/20260918/downloads/舊影片標題.mp4"
            download_fn = server.make_real_download_fn(fake_orchestrator)
            payload = {"url": "https://www.youtube.com/watch?v=pfGg0Uris1w", "format_type": "video",
                       "want_srt": True, "skip_existing": True}
            download_fn(payload, _not_cancelled(), mock.Mock(), fake_report_progress)

        message = fake_report_progress.call_args.args[1]
        self.assertIn("C:/fake/20260918/downloads/舊影片標題.mp4", message)

    def test_already_archived_video_reports_a_clear_message_instead_of_silent_done(self):
        """使用者實測踩到的 bug:網址對應的影片已經在 yt-dlp 的 download_archive
        裡記錄過,download_media() 會回傳空陣列(不下載、不報錯)。這裡不能讓
        它看起來跟正常完成一模一樣——沒有任何檔案、也沒有接轉錄工作,卻顯示
        「完成」,使用者完全不知道發生了什麼事。"""
        fake_orchestrator = mock.Mock()
        fake_report_progress = mock.Mock()

        with mock.patch.object(server, "download_media", return_value=[]), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "fetch_title_only", return_value=None), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260926")):
            download_fn = server.make_real_download_fn(fake_orchestrator)
            payload = {"url": "https://youtube.com/watch?v=x", "format_type": "video",
                       "want_srt": True, "skip_existing": True}
            download_fn(payload, _not_cancelled(), mock.Mock(), fake_report_progress)

        fake_orchestrator.enqueue_transcription.assert_not_called()
        fake_record.save.assert_not_called()
        fake_report_progress.assert_called_once_with(100.0, mock.ANY, final=True)
        message = fake_report_progress.call_args.args[1]
        self.assertIn("已下載過", message)


def _entry(name, vid):
    return {"path": f"C:/fake/d/downloads/{name}.mp4", "title": name, "channel": "頻道",
            "url": f"https://www.youtube.com/watch?v={vid}", "upload_date": None, "video_id": vid}


class RateLimitMidDownloadKeepsFinishedItemsTest(unittest.TestCase):
    """使用者實測:頻道/播放清單網址下載到一半被 YouTube 限流,已經下載完的那幾支的
    來源和自動轉錄全部被丟掉,只能手動轉錄、變成「本機上傳」。"""

    def _download(self, fake_download_media, sidecar_error=None):
        fake_orchestrator = mock.Mock()
        with mock.patch.object(server, "download_media", side_effect=fake_download_media), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/d")), \
             mock.patch("traceback.print_exc"):
            if sidecar_error:
                fake_sidecar.write.side_effect = sidecar_error
            error = None
            try:
                server.make_real_download_fn(fake_orchestrator)(
                    {"url": "https://www.youtube.com/@channel", "want_srt": True, "skip_existing": True},
                    _not_cancelled(), mock.Mock(), mock.Mock())
            except Exception as e:  # noqa: BLE001 - 測試要檢查丟出來的是什麼
                error = e
        queued = [c.args[0]["path"] for c in fake_orchestrator.enqueue_transcription.call_args_list]
        return error, queued, fake_record, fake_sidecar

    def test_items_finished_before_the_rate_limit_are_recorded_and_queued(self):
        from orchestrator import RateLimited
        a, b = _entry("第一支", "aaaaaaaaaaa"), _entry("第二支", "bbbbbbbbbbb")

        def fake_download_media(url, **kwargs):
            kwargs["on_item_done"](a)
            kwargs["on_item_done"](b)
            raise RateLimited("HTTP Error 429")

        error, queued, fake_record, fake_sidecar = self._download(fake_download_media)
        self.assertIsInstance(error, RateLimited)   # 工作本身照樣變「待重試」
        self.assertEqual(queued, [a["path"], b["path"]])
        self.assertEqual(fake_record.save.call_args_list, [mock.call([a]), mock.call([b])])
        self.assertEqual([c.args[0] for c in fake_sidecar.write.call_args_list], [a["path"], b["path"]])

    def test_a_returned_entry_for_an_already_reported_video_at_another_path_is_ignored(self):
        # download_media 的「前 15 字比對」備用找法可能找到「另一集」的檔案(系列影片標題開頭都一樣),
        # 回傳的 path 跟即時回報的不同但是同一支影片。不能在那個錯的檔案旁寫這支的資訊檔
        a = _entry("系列第一集", "aaaaaaaaaaa")
        wrong_file = dict(a, path="C:/fake/d/downloads/系列第二集.mp4")

        def fake_download_media(url, **kwargs):
            kwargs["on_item_done"](a)
            return [wrong_file]

        error, queued, _r, fake_sidecar = self._download(fake_download_media)
        self.assertIsNone(error)
        self.assertEqual(queued, [a["path"]])
        self.assertEqual([c.args[0] for c in fake_sidecar.write.call_args_list], [a["path"]])

    def test_an_item_both_reported_and_returned_is_handled_once(self):
        a = _entry("第一支", "aaaaaaaaaaa")
        rescued = {"path": "C:/fake/d/downloads/救回來的.mp4", "title": "救回來的", "channel": None,
                   "url": None, "upload_date": None, "video_id": None}

        def fake_download_media(url, **kwargs):
            kwargs["on_item_done"](a)
            return [a, rescued]   # 檔案鎖定重試救回來的那支不會經過即時回報

        error, queued, _r, _s = self._download(fake_download_media)
        self.assertIsNone(error)
        self.assertEqual(queued, [a["path"], rescued["path"]])

    def test_failing_to_write_the_sidecar_does_not_stop_the_transcription_being_queued(self):
        a = _entry("第一支", "aaaaaaaaaaa")
        error, queued, _r, _s = self._download(lambda url, **kwargs: [a], sidecar_error=OSError("disk full"))
        self.assertIsNone(error)
        self.assertEqual(queued, [a["path"]])


class LookupSourceTest(unittest.TestCase):
    """加入本機檔案時就查來源,按開始之前使用者就看得到:影片旁的資訊檔 → 下載記錄
    → 用檔名反查 YouTube(結果標成「自動找到,請確認」,由使用者決定)→ 找不到。"""

    META = {"title": "t", "channel": "c", "url": "https://www.youtube.com/watch?v=ccccccccccc", "upload_date": None}

    def _lookup(self, sidecar=None, record=None, guessed=None):
        with mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "source_lookup") as fake_lookup:
            fake_sidecar.read.return_value = sidecar
            fake_record.lookup.return_value = record
            fake_lookup.find_metadata.return_value = guessed
            return server.lookup_source("C:/fake/video.mp4"), fake_lookup

    def test_sidecar_first_and_no_network(self):
        result, fake_lookup = self._lookup(sidecar=self.META, record={"url": "x"}, guessed={"url": "y"})
        self.assertEqual(result, {"status": "sidecar", "metadata": self.META})
        fake_lookup.find_metadata.assert_not_called()

    def test_then_the_download_record(self):
        result, fake_lookup = self._lookup(record=self.META, guessed={"url": "y"})
        self.assertEqual(result, {"status": "record", "metadata": self.META})
        fake_lookup.find_metadata.assert_not_called()

    def test_then_a_youtube_guess_for_the_user_to_confirm(self):
        result, _ = self._lookup(guessed=self.META)
        self.assertEqual(result, {"status": "guessed", "metadata": self.META})

    def test_nothing_found(self):
        result, _ = self._lookup()
        self.assertEqual(result, {"status": "none", "metadata": None})

    def test_http_route(self):
        import json
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(mock.Mock()))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(server, "lookup_source", return_value={"status": "none", "metadata": None}) as fake:
                req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/lookup-source",
                                             data=json.dumps({"path": "C:/x/影片.mp4"}).encode("utf-8"), method="POST")
                body = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
            fake.assert_called_once_with("C:/x/影片.mp4")
            self.assertEqual(body, {"status": "none", "metadata": None})
        finally:
            srv.shutdown()
            srv.server_close()


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
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "download_record") as fake_record:
            fake_sidecar.read.return_value = None
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
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "download_record") as fake_record:
            fake_sidecar.read.return_value = None
            fake_record.lookup.return_value = recorded_metadata
            transcribe_fn = server.make_real_transcribe_fn()
            # payload 沒有 metadata 這個鍵,模擬使用者手動挑本機檔案
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": False},
                          not_cancelled, mock.Mock(), mock.Mock())

        fake_record.lookup.assert_called_once_with("C:/fake/video.mp4")
        fake_transcriber.save_transcript.assert_called_once_with("完整結果", mock.ANY, metadata=recorded_metadata)

    def test_does_not_search_youtube_at_transcription_time(self):
        # 使用者決定:找不到來源時由他在加入清單時確認/提供;轉錄時自動去猜,
        # 等於蓋過他「留空=本機檔案」的決定
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("文字", [])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False
        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "source_lookup") as fake_lookup:
            fake_record.lookup.return_value = None
            fake_sidecar.read.return_value = None
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": False},
                          not_cancelled, mock.Mock(), mock.Mock())
        fake_lookup.find_metadata.assert_not_called()
        fake_transcriber.save_transcript.assert_called_once_with("文字", mock.ANY, metadata=None)


class TranscriptionSourceOrderTest(unittest.TestCase):
    """轉錄前找來源的順序(使用者定的):工作自帶的 → 使用者貼的網址 → 影片旁的資訊檔
    → 下載記錄 → 都沒有就當本機檔案。找到的會寫成影片旁的資訊檔,下次不用再查。"""

    META = {"title": "舊影片", "channel": "頻道C", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            "upload_date": "2026-09-01"}

    def _run(self, payload, sidecar=None, record=None, fetched=None):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("文字", [])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False
        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")), \
             mock.patch.object(server, "download_record") as fake_record, \
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "fetch_metadata", return_value=fetched) as fake_fetch:
            fake_sidecar.read.return_value = sidecar
            fake_record.lookup.return_value = record
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn(dict({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": False}, **payload),
                          not_cancelled, mock.Mock(), mock.Mock())
        used = fake_transcriber.save_transcript.call_args.kwargs["metadata"]
        return used, fake_sidecar, fake_record, fake_fetch

    def test_sidecar_next_to_the_video_is_used_first(self):
        used, fake_sidecar, fake_record, _f = self._run({}, sidecar=self.META, record={"url": "https://other"})
        self.assertEqual(used, self.META)
        fake_record.lookup.assert_not_called()
        fake_sidecar.write.assert_not_called()   # 已經有了,不重寫

    def test_download_record_is_the_fallback_and_its_result_becomes_a_sidecar(self):
        used, fake_sidecar, _r, _f = self._run({}, sidecar=None, record=self.META)
        self.assertEqual(used, self.META)
        fake_sidecar.write.assert_called_once_with("C:/fake/video.mp4", self.META)

    def test_a_url_pasted_by_the_user_is_looked_up_and_saved_next_to_the_video(self):
        used, fake_sidecar, _r, fake_fetch = self._run(
            {"source_url": self.META["url"]}, sidecar={"url": "https://stale"}, fetched=self.META)
        fake_fetch.assert_called_once_with(self.META["url"])
        self.assertEqual(used, self.META)   # 使用者當下給的,優先於影片旁舊的資訊檔
        fake_sidecar.write.assert_called_once_with("C:/fake/video.mp4", self.META)

    def test_a_url_only_result_does_not_overwrite_a_richer_sidecar_for_the_same_video(self):
        # 抓不到詳細資訊時只剩網址;如果影片旁本來就有同一支影片的完整資訊,要用那份,不能蓋掉
        used, fake_sidecar, _r, _f = self._run({"source_url": self.META["url"]}, sidecar=self.META, fetched=None)
        self.assertEqual(used, self.META)
        fake_sidecar.write.assert_not_called()

    def test_the_same_video_pasted_in_another_url_form_still_keeps_the_richer_sidecar(self):
        # 資訊檔存的是標準網址;使用者貼帶 &list= 或 youtu.be 短網址的同一支影片,也要認得
        for pasted in ("https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PLxyz", "https://youtu.be/aaaaaaaaaaa"):
            used, fake_sidecar, _r, _f = self._run({"source_url": pasted}, sidecar=self.META, fetched=None)
            self.assertEqual(used, self.META, pasted)
            fake_sidecar.write.assert_not_called()

    def test_a_pasted_channel_or_playlist_url_is_not_recorded_as_the_source(self):
        # 抓不到資訊、網址又不是單一影片(頻道/播放清單):不採用,照原順序往下找(這裡是影片旁的資訊檔)
        used, fake_sidecar, _r, _f = self._run(
            {"source_url": "https://www.youtube.com/@somechannel/videos"}, sidecar=self.META, fetched=None)
        self.assertEqual(used, self.META)
        fake_sidecar.write.assert_not_called()
        used, fake_sidecar, _r, _f = self._run(
            {"source_url": "https://www.youtube.com/playlist?list=PLxyz"}, sidecar=None, fetched=None)
        self.assertIsNone(used)
        fake_sidecar.write.assert_not_called()

    def test_a_pasted_url_is_kept_even_if_its_details_cannot_be_fetched(self):
        used, _sc, _r, _f = self._run({"source_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"}, fetched=None)
        self.assertEqual(used["url"], "https://www.youtube.com/watch?v=bbbbbbbbbbb")

    def test_metadata_carried_by_the_job_wins_and_is_saved_next_to_the_video(self):
        used, fake_sidecar, _r, _f = self._run({"metadata": self.META}, sidecar=None)
        self.assertEqual(used, self.META)
        fake_sidecar.write.assert_called_once_with("C:/fake/video.mp4", self.META)


def _not_cancelled():
    flag = mock.Mock()
    flag.is_set.return_value = False
    flag.wait.return_value = False   # Event.wait():等滿時間沒被取消回傳 False
    return flag


def _listed(name, vid, channel="頻道"):
    return {"url": f"https://www.youtube.com/watch?v={vid}", "title": name, "video_id": vid,
            "channel": channel, "upload_date": None}


class ListedDownloadDecisionsTest(unittest.TestCase):
    """下載前先列清單、逐支判斷:已有逐字稿的不下載、已下載沒轉錄的補排轉錄、
    只下載不轉錄的不排轉錄。判斷的依據是掃描所有日期資料夾的總清單。"""

    def _run(self, entries, known=None, payload=None, downloaded=None, cancel_after=None, cancelled_while_waiting=False,
             queued_paths=None, download_error_on=None):
        known = known or ledger.Ledger()
        fake_orchestrator = mock.Mock()
        report = mock.Mock()
        cancel = _not_cancelled()
        cancel.wait.return_value = cancelled_while_waiting
        self.cancel = cancel
        self.error = None
        calls = []

        def fake_download_media(url, **kwargs):
            calls.append(url)
            if cancel_after is not None and len(calls) >= cancel_after:
                cancel.is_set.return_value = True
            if download_error_on is not None and len(calls) == download_error_on:
                from orchestrator import RateLimited
                raise RateLimited("429")
            entry = (downloaded or {}).get(url)
            if entry:
                kwargs["on_item_done"](entry)
                return [entry]
            return []

        with mock.patch.object(server, "list_entries", return_value=entries), \
             mock.patch.object(server.ledger, "scan", return_value=known), \
             mock.patch.object(server.ledger, "write_summary") as fake_summary, \
             mock.patch.object(server, "download_media", side_effect=fake_download_media), \
             mock.patch.object(server, "download_record"), \
             mock.patch.object(server, "source_sidecar") as fake_sidecar, \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20261001")):
            run = server.make_real_download_fn(fake_orchestrator, queued_paths=queued_paths)
            try:
                run(dict({"url": "https://www.youtube.com/@ch/videos", "want_srt": True, "skip_existing": True},
                         **(payload or {})), cancel, mock.Mock(), report)
            except Exception as e:  # noqa: BLE001 - 限流測試要看丟出來的是什麼
                self.error = e
        queued = [c.args[0] for c in fake_orchestrator.enqueue_transcription.call_args_list]
        return calls, queued, report, fake_sidecar, fake_summary

    def _known(self, transcripts=(), downloads=()):
        k = ledger.Ledger()
        for stem in transcripts:
            k.transcripts[stem] = ["20260924"]
        for stem, path in downloads:
            k.downloads[stem] = [("20260924", path)]
        return k

    def test_a_video_that_already_has_a_transcript_is_not_downloaded(self):
        a, b = _listed("有逐字稿", "aaaaaaaaaaa"), _listed("全新的", "bbbbbbbbbbb")
        new = {"path": "C:/fake/20261001/downloads/全新的.mp4", **{k: b[k] for k in ("title", "channel", "upload_date")},
               "url": b["url"], "video_id": "bbbbbbbbbbb"}
        calls, queued, _r, _s, _f = self._run([a, b], known=self._known(transcripts=["有逐字稿"]),
                                              downloaded={b["url"]: new})
        self.assertEqual(calls, [b["url"]])
        self.assertEqual([q["path"] for q in queued], [new["path"]])

    def test_the_transcript_check_can_be_turned_off(self):
        a = _listed("有逐字稿", "aaaaaaaaaaa")
        calls, _q, _r, _s, _f = self._run([a], known=self._known(transcripts=["有逐字稿"]),
                                          payload={"skip_transcribed": False})
        self.assertEqual(calls, [a["url"]])

    def test_an_already_downloaded_video_without_a_transcript_gets_its_transcription_queued(self):
        a = _listed("下載過沒轉錄", "aaaaaaaaaaa")
        old = "C:/fake/20260924/downloads/下載過沒轉錄.mp4"
        calls, queued, _r, _s, _f = self._run([a], known=self._known(downloads=[("下載過沒轉錄", old)]))
        self.assertEqual(calls, [])                      # 不重新下載
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["path"], old)
        self.assertEqual(queued[0]["metadata"]["url"], a["url"])
        self.assertEqual(queued[0]["metadata"]["title"], "下載過沒轉錄")
        self.assertTrue(queued[0]["want_srt"])

    def test_an_archived_video_without_a_transcript_is_neither_downloaded_nor_transcribed(self):
        # 搬到歸檔資料夾的影片:算「下載過」(不重抓),但不替使用者從歸檔資料夾補轉錄
        a = _listed("歸檔的", "aaaaaaaaaaa")
        known = self._known(downloads=[("歸檔的", "F:/archive/2026-09-30 頻道/Video/歸檔的.mp4")])
        known.archived_labels.add("20260924")      # _known 把下載標成 20260924
        calls, queued, _r, _s, _f = self._run([a], known=known)
        self.assertEqual(calls, [])
        self.assertEqual(queued, [])

    def test_download_only_never_queues_a_transcription(self):
        a, b = _listed("下載過沒轉錄", "aaaaaaaaaaa"), _listed("全新的", "bbbbbbbbbbb")
        new = {"path": "C:/fake/20261001/downloads/全新的.mp4", "title": "全新的", "channel": "頻道",
               "url": b["url"], "upload_date": None, "video_id": "bbbbbbbbbbb"}
        calls, queued, _r, fake_sidecar, _f = self._run(
            [a, b], known=self._known(downloads=[("下載過沒轉錄", "C:/fake/20260924/downloads/下載過沒轉錄.mp4")]),
            payload={"download_only": True}, downloaded={b["url"]: new})
        self.assertEqual(calls, [b["url"]])
        self.assertEqual(queued, [])
        fake_sidecar.write.assert_called_once_with(new["path"], new)   # 影片旁的資訊檔照寫

    def test_nothing_new_to_download_says_so_instead_of_pretending(self):
        a = _listed("有逐字稿", "aaaaaaaaaaa")
        _c, _q, report, _s, _f = self._run([a], known=self._known(transcripts=["有逐字稿"]))
        message = report.call_args.args[1]
        self.assertIn("略過 1 支", message)

    def test_cancelling_stops_before_the_next_video(self):
        a, b = _listed("甲", "aaaaaaaaaaa"), _listed("乙", "bbbbbbbbbbb")
        calls, _q, _r, _s, _f = self._run([a, b], cancel_after=1)
        self.assertEqual(calls, [a["url"]])

    def _two_new(self):
        a, b = _listed("甲", "aaaaaaaaaaa"), _listed("乙", "bbbbbbbbbbb")
        got = {x["url"]: {"path": f"C:/fake/20261001/downloads/{x['title']}.mp4", "title": x["title"], "channel": "頻道",
                          "url": x["url"], "upload_date": None, "video_id": x["video_id"]} for x in (a, b)}
        return a, b, got

    def test_it_waits_between_downloads_but_not_before_the_first_or_after_the_last(self):
        a, b, got = self._two_new()
        calls, _q, _r, _s, _f = self._run([a, b], payload={"gap_minutes": 5}, downloaded=got)
        self.assertEqual(calls, [a["url"], b["url"]])
        self.cancel.wait.assert_called_once_with(300)

    def test_the_default_wait_is_five_minutes_and_zero_turns_it_off(self):
        a, b, got = self._two_new()
        self._run([a, b], downloaded=got)
        self.cancel.wait.assert_called_once_with(300)
        self._run([a, b], payload={"gap_minutes": 0}, downloaded=got)
        self.cancel.wait.assert_not_called()

    def test_skipped_videos_do_not_cause_waiting(self):
        a, b, got = self._two_new()
        calls, _q, _r, _s, _f = self._run([a, b], known=self._known(transcripts=["甲"]), downloaded=got)
        self.assertEqual(calls, [b["url"]])
        self.cancel.wait.assert_not_called()

    def test_cancelling_during_the_wait_stops_right_away(self):
        a, b, got = self._two_new()
        calls, _q, _r, _s, _f = self._run([a, b], downloaded=got, cancelled_while_waiting=True)
        self.assertEqual(calls, [a["url"]])   # 乙沒有被下載


    def test_a_second_job_does_not_queue_the_same_file_again_while_the_first_transcription_is_pending(self):
        # 總清單只在工作開頭掃一次:第一個工作排的轉錄還沒做完,檔案看起來還是「下載過、沒逐字稿」
        a = _listed("下載過沒轉錄", "aaaaaaaaaaa")
        known = self._known(downloads=[("下載過沒轉錄", "C:/fake/20260924/downloads/下載過沒轉錄.mp4")])
        shared = set()
        _c, first, _r, _s, _f = self._run([a], known=known, queued_paths=shared)
        _c, second, _r, _s, _f = self._run([a], known=known, queued_paths=shared)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_a_video_that_yt_dlp_silently_skipped_does_not_cause_a_wait(self):
        # download_media 回傳空(影片在 archive 裡但檔案不見了):沒有真的下載,下一支不用等
        a, b, got = self._two_new()
        calls, _q, _r, _s, _f = self._run([a, b], downloaded={b["url"]: got[b["url"]]})
        self.assertEqual(calls, [a["url"], b["url"]])
        self.cancel.wait.assert_not_called()

    def test_a_bad_wait_setting_falls_back_to_the_default_instead_of_crashing_the_job(self):
        a, b, got = self._two_new()
        self._run([a, b], payload={"gap_minutes": "abc"}, downloaded=got)
        self.cancel.wait.assert_called_once_with(300)
        self._run([a, b], payload={"gap_minutes": -3}, downloaded=got)
        self.cancel.wait.assert_not_called()          # 負數當 0

    def test_the_summary_is_refreshed_at_the_end_even_when_the_job_is_cut_short_by_a_rate_limit(self):
        a, b, got = self._two_new()
        _c, _q, _r, _s, fake_summary = self._run([a, b], payload={"gap_minutes": 0}, downloaded=got, download_error_on=2)
        self.assertEqual(type(self.error).__name__, "RateLimited")
        self.assertTrue(fake_summary.called)

    def test_the_summary_is_refreshed_once_per_job_not_after_every_video(self):
        a, b, got = self._two_new()
        _c, _q, _r, _s, fake_summary = self._run([a, b], payload={"gap_minutes": 0}, downloaded=got)
        self.assertEqual(fake_summary.call_count, 1)

    def test_the_summary_file_is_refreshed_when_a_video_finishes(self):
        a = _listed("全新的", "aaaaaaaaaaa")
        new = {"path": "C:/fake/20261001/downloads/全新的.mp4", "title": "全新的", "channel": "頻道",
               "url": a["url"], "upload_date": None, "video_id": "aaaaaaaaaaa"}
        _c, _q, _r, _s, fake_summary = self._run([a], downloaded={a["url"]: new})
        self.assertTrue(fake_summary.called)


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


class DownloadRouteOptionsTest(unittest.TestCase):
    """畫面上的「只下載不轉錄」「略過已有逐字稿的影片」要真的傳到下載工作裡。"""

    def _post(self, body):
        import json
        from http.server import ThreadingHTTPServer
        fake_orchestrator = mock.Mock()
        fake_orchestrator.enqueue_download.return_value = "1"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(fake_orchestrator))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/jobs/download",
                                         data=json.dumps(body).encode("utf-8"), method="POST")
            urllib.request.urlopen(req)
        finally:
            srv.shutdown()
            srv.server_close()
        return fake_orchestrator.enqueue_download.call_args.args[0]

    def test_the_two_options_are_passed_through(self):
        payload = self._post({"url": "https://youtube.com/@x", "download_only": True, "skip_transcribed": False})
        self.assertIs(payload["download_only"], True)
        self.assertIs(payload["skip_transcribed"], False)

    def test_the_wait_between_downloads_defaults_to_five_minutes_and_can_be_changed(self):
        self.assertEqual(self._post({"url": "https://youtube.com/@x"})["gap_minutes"], 5)
        self.assertEqual(self._post({"url": "https://youtube.com/@x", "gap_minutes": 0})["gap_minutes"], 0)

    def test_defaults_are_download_and_transcribe_and_skip_videos_with_a_transcript(self):
        payload = self._post({"url": "https://youtube.com/@x"})
        self.assertIs(payload["download_only"], False)
        self.assertIs(payload["skip_transcribed"], True)


class AutoRetrySettingTest(unittest.TestCase):
    """使用者定的:被 YouTube 限流後,等 6 小時自動重試,失敗再等 6 小時。畫面要看得到下一次重試的時間。"""

    def test_the_real_orchestrator_retries_every_six_hours(self):
        with mock.patch.object(server, "make_real_transcribe_fn", return_value=lambda *a: None):
            orch = server.build_orchestrator()
        try:
            self.assertEqual(orch.auto_retry_seconds, 6 * 60 * 60)
        finally:
            orch.shutdown()

    def test_the_jobs_list_tells_the_screen_when_the_next_retry_is(self):
        import json
        from http.server import ThreadingHTTPServer
        fake_orchestrator = mock.Mock()
        fake_orchestrator.list_jobs.return_value = []
        fake_orchestrator.auto_retry_at = 1790000000.5
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(fake_orchestrator))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/jobs"))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(data["auto_retry_at"], 1790000000.5)


class FinalStatusMessagesTest(unittest.TestCase):
    """使用者實測畫面:工作「完成」了,狀態底下卻還寫「下載中」「轉錄中」。
    結束時要明確留下「做完了」這句話(final=True),orchestrator 才會用它取代進度文字。"""

    def test_completed_download_leaves_a_final_done_message(self):
        report = mock.Mock()
        with mock.patch.object(server, "download_media", return_value=[{"path": "C:/fake/a.mp4"}]), \
             mock.patch.object(server, "download_record"), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260929")):
            download_fn = server.make_real_download_fn(mock.Mock())
            download_fn({"url": "https://youtube.com/watch?v=x"}, _not_cancelled(), mock.Mock(), report)
        report.assert_any_call(100.0, "下載完成", final=True)

    def test_completed_transcription_leaves_a_final_done_message(self):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("文字", [{"start": 0, "end": 1, "text": "文字"}])
        not_cancelled = mock.Mock()
        not_cancelled.is_set.return_value = False
        report = mock.Mock()
        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "download_record"), \
             mock.patch.object(server.source_sidecar, "read", return_value=None), \
             mock.patch.object(server.source_sidecar, "write"), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260929")):
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": False},
                          not_cancelled, mock.Mock(), report)
        report.assert_any_call(100.0, "轉錄完成", final=True)

    def test_cancelled_transcription_message_is_final(self):
        fake_transcriber = mock.Mock()
        fake_transcriber.transcribe.return_value = ("部分", [])
        cancelled = mock.Mock()
        cancelled.is_set.return_value = True
        report = mock.Mock()
        with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260929")):
            transcribe_fn = server.make_real_transcribe_fn()
            transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": True, "skip_existing": False},
                          cancelled, mock.Mock(), report)
        report.assert_any_call(0.0, "已取消,不保留部分結果", final=True)

    def test_skipping_an_existing_transcript_message_is_final(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp)
            (day / "transcripts").mkdir()
            (day / "transcripts" / "video.txt").write_text("已經有了", encoding="utf-8")
            report = mock.Mock()
            with mock.patch.object(server, "Transcriber", return_value=mock.Mock()), \
             mock.patch.object(server, "today_dir", return_value=day):
                transcribe_fn = server.make_real_transcribe_fn()
                transcribe_fn({"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": True},
                              mock.Mock(), mock.Mock(), report)
        report.assert_any_call(100.0, "已有逐字稿,跳過", final=True)


    def test_a_transcript_made_on_another_day_also_counts_as_existing(self):
        # 逐字稿在別天的資料夾(例如重開機跨日),轉錄前也要認得,不重做
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "20260924" / "transcripts").mkdir(parents=True)
            (base / "20260924" / "transcripts" / "video.txt").write_text("昨天做過", encoding="utf-8")
            today = base / "20261001"
            (today / "transcripts").mkdir(parents=True)
            report = mock.Mock()
            fake_transcriber = mock.Mock()
            with mock.patch.object(server, "Transcriber", return_value=fake_transcriber),                  mock.patch.object(server, "BASE_DIR", base),                  mock.patch.object(server, "today_dir", return_value=today):
                server.make_real_transcribe_fn()(
                    {"path": "C:/fake/video.mp4", "want_srt": False, "skip_existing": True},
                    mock.Mock(), mock.Mock(), report)
        report.assert_any_call(100.0, "已有逐字稿,跳過", final=True)
        fake_transcriber.transcribe.assert_not_called()


class JobJsonTest(unittest.TestCase):
    def test_started_at_is_exposed_so_the_screen_can_show_elapsed_time(self):
        from orchestrator import Job, JobType
        job = Job("1", JobType.TRANSCRIBE, {"path": "C:/x.mp4"})
        job.started_at = 1234.5
        self.assertEqual(server._job_to_dict(job)["started_at"], 1234.5)


class AutoOpenFoldersTest(unittest.TestCase):
    """舊版 3-4 每批做完會自動打開對應資料夾,改寫時漏掉了。"""

    def _run_batch(self, enqueue):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp)
            fake_transcriber = mock.Mock()
            fake_transcriber.transcribe.return_value = ("文字", [{"start": 0, "end": 1, "text": "文字"}])
            with mock.patch.object(server, "Transcriber", return_value=fake_transcriber), \
             mock.patch.object(server, "download_media",
                                   return_value=[{"path": str(day / "downloads" / "a.mp4")}]), \
             mock.patch.object(server, "download_record"), \
             mock.patch.object(server.source_sidecar, "read", return_value=None), \
             mock.patch.object(server.source_sidecar, "write"), \
             mock.patch.object(server, "build_day_index"), \
             mock.patch.object(server, "today_dir", return_value=day), \
             mock.patch.object(server, "_open_folder") as fake_open:
                orch = server.build_orchestrator()
                enqueue(orch)
                orch.wait_idle()
                orch.shutdown()
            return day, fake_open

    def test_a_finished_transcription_refreshes_the_summary_file(self):
        with mock.patch.object(server.ledger, "write_summary") as fake_summary:
            self._run_batch(lambda orch: orch.enqueue_transcription(
                {"path": "C:/fake/v.mp4", "want_srt": False, "skip_existing": False}))
        self.assertTrue(fake_summary.called)

    def test_local_file_transcription_opens_only_the_transcripts_folder(self):
        day, fake_open = self._run_batch(lambda orch: orch.enqueue_transcription(
            {"path": "C:/fake/v.mp4", "want_srt": False, "skip_existing": False}))
        fake_open.assert_called_once_with(day / "transcripts")

    def test_download_with_its_chained_transcription_opens_both_folders_once(self):
        day, fake_open = self._run_batch(lambda orch: orch.enqueue_download(
            {"url": "https://www.youtube.com/watch?v=abcdefghijk", "want_srt": False, "skip_existing": False}))
        self.assertEqual(fake_open.call_args_list,
                         [mock.call(day / "downloads"), mock.call(day / "transcripts")])


class OpenFolderTest(unittest.TestCase):
    def test_opens_an_existing_folder_with_the_file_explorer(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(server.os, "startfile", create=True) as fake_startfile:
            server._open_folder(Path(tmp))
        fake_startfile.assert_called_once_with(Path(tmp))

    def test_does_nothing_when_the_folder_does_not_exist(self):
        with mock.patch.object(server.os, "startfile", create=True) as fake_startfile:
            server._open_folder(Path("C:/definitely/not/here"))
        fake_startfile.assert_not_called()

    def test_a_failure_to_open_never_breaks_the_job(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(server.os, "startfile", create=True, side_effect=OSError("no explorer")):
            server._open_folder(Path(tmp))  # 不能丟例外


if __name__ == "__main__":
    unittest.main()
