import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downloader  # noqa: E402
from orchestrator import RateLimited  # noqa: E402


class ListEntriesTest(unittest.TestCase):
    """下載前先「只列清單、不下載」:拿到每支影片的標題和網址,才能在下載前就判斷
    哪些已經有逐字稿、哪些已經下載過。"""

    def _list(self, info=None, error=None, url="https://www.youtube.com/@ch/videos"):
        fake_ydl = mock.MagicMock()
        if error:
            fake_ydl.__enter__.return_value.extract_info.side_effect = error
        else:
            fake_ydl.__enter__.return_value.extract_info.return_value = info
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_ydl) as cls:
            return downloader.list_entries(url), cls

    def test_a_channel_gives_one_item_per_video_and_only_lists(self):
        info = {"_type": "playlist", "entries": [
            {"id": "aaaaaaaaaaa", "title": "甲", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "channel": "頻道"},
            {"id": "bbbbbbbbbbb", "title": "乙", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "uploader": "上傳者"},
        ]}
        result, cls = self._list(info)
        self.assertEqual(result, [
            {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "甲", "video_id": "aaaaaaaaaaa",
             "channel": "頻道", "upload_date": None},
            {"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "title": "乙", "video_id": "bbbbbbbbbbb",
             "channel": "上傳者", "upload_date": None},
        ])
        params = cls.call_args.args[0]
        self.assertEqual(params.get("extract_flat"), "in_playlist")   # 只列清單,不逐支展開
        self.assertTrue(params.get("skip_download"))

    def test_a_single_video_url_gives_one_item(self):
        info = {"id": "aaaaaaaaaaa", "title": "單支", "webpage_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
                "channel": "頻道", "upload_date": "20260901"}
        result, _ = self._list(info, url="https://www.youtube.com/watch?v=aaaaaaaaaaa")
        self.assertEqual(result, [{"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "單支",
                                   "video_id": "aaaaaaaaaaa", "channel": "頻道", "upload_date": "2026-09-01"}])

    def test_nested_playlists_are_flattened_and_empty_slots_skipped(self):
        info = {"_type": "playlist", "entries": [
            None,
            {"_type": "playlist", "entries": [
                {"id": "aaaaaaaaaaa", "title": "巢內", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}]},
        ]}
        result, _ = self._list(info)
        self.assertEqual([e["title"] for e in result], ["巢內"])

    def test_returns_none_when_the_list_cannot_be_read(self):
        result, _ = self._list(error=Exception("network down"))
        self.assertIsNone(result)

    def test_a_rate_limit_while_listing_is_raised_so_the_job_waits_for_retry(self):
        with self.assertRaises(RateLimited):
            self._list(error=Exception("HTTP Error 429: Too Many Requests"))


if __name__ == "__main__":
    unittest.main()
