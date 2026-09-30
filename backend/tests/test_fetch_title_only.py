import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downloader  # noqa: E402


class ExtractVideoIdTest(unittest.TestCase):
    def test_extracts_from_watch_url(self):
        self.assertEqual(downloader.extract_video_id("https://www.youtube.com/watch?v=pfGg0Uris1w"), "pfGg0Uris1w")

    def test_extracts_from_short_url(self):
        self.assertEqual(downloader.extract_video_id("https://youtu.be/pfGg0Uris1w"), "pfGg0Uris1w")

    def test_extracts_from_shorts_url(self):
        self.assertEqual(downloader.extract_video_id("https://www.youtube.com/shorts/pfGg0Uris1w"), "pfGg0Uris1w")

    def test_returns_none_for_unrecognized_url(self):
        self.assertIsNone(downloader.extract_video_id("https://example.com/not-youtube"))


class FetchMetadataTest(unittest.TestCase):
    """使用者貼了 YouTube 網址當來源時,要抓回標題、頻道、上傳日期寫進逐字稿表頭;
    只查資訊、不下載。"""

    def _fetch(self, info=None, error=None):
        fake_ydl = mock.MagicMock()
        if error:
            fake_ydl.__enter__.return_value.extract_info.side_effect = error
        else:
            fake_ydl.__enter__.return_value.extract_info.return_value = info
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            result = downloader.fetch_metadata("https://www.youtube.com/watch?v=aaaaaaaaaaa")
        return result, fake_ydl

    def test_returns_title_channel_url_and_formatted_upload_date(self):
        result, fake_ydl = self._fetch({"title": "標題", "channel": "頻道", "upload_date": "20260725",
                                        "webpage_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"})
        self.assertEqual(result, {"title": "標題", "channel": "頻道", "upload_date": "2026-07-25",
                                  "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"})
        self.assertEqual(fake_ydl.__enter__.return_value.extract_info.call_args.kwargs, {"download": False})

    def test_only_looks_at_the_single_video_never_a_whole_playlist_or_channel(self):
        # 使用者貼的網址常帶 &list=;不設 noplaylist 會把整個播放清單(甚至整個頻道)每支都查一遍,
        # 拿到的還是播放清單的標題,而且容易被限流
        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = {"title": "t", "webpage_url": "https://x"}
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_ydl) as fake_cls:
            downloader.fetch_metadata("https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PLxyz")
        params = fake_cls.call_args.args[0]
        self.assertTrue(params.get("noplaylist"))
        self.assertEqual(params.get("extract_flat"), "in_playlist")

    def test_a_playlist_or_channel_result_is_not_treated_as_one_video(self):
        result, _ = self._fetch({"_type": "playlist", "title": "某頻道 - Videos", "entries": [],
                                 "webpage_url": "https://www.youtube.com/@channel/videos"})
        self.assertIsNone(result)

    def test_returns_none_on_failure(self):
        result, _ = self._fetch(error=Exception("Sign in to confirm you're not a bot"))
        self.assertIsNone(result)


class FetchTitleOnlyTest(unittest.TestCase):
    """已經被 download_archive 記錄過的影片,download_media() 的
    extract_info(download=True) 會回傳 None,拿不到標題。這個函式用
    download=False 另外查,archive 只在下載階段生效,不影響這個呼叫。"""

    def test_returns_title_from_extracted_info(self):
        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = {"title": "測試影片標題"}
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            result = downloader.fetch_title_only("https://www.youtube.com/watch?v=x")
        self.assertEqual(result, "測試影片標題")

    def test_returns_none_when_extraction_fails(self):
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", side_effect=Exception("network error")):
            result = downloader.fetch_title_only("https://www.youtube.com/watch?v=x")
        self.assertIsNone(result)

    def test_returns_none_when_info_is_none(self):
        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = None
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            result = downloader.fetch_title_only("https://www.youtube.com/watch?v=x")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
