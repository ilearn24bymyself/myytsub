import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downloader  # noqa: E402

FFMPEG = Path(__file__).resolve().parent.parent / "bin" / "ffmpeg.exe"


class _FileUrlYoutubeDL(downloader.yt_dlp.YoutubeDL):
    """測試用:允許 file:// 網址,讓真的 yt-dlp 跑完整流程(下載→轉 mp3→搬到最終位置)
    而不用連網路。"""

    def __init__(self, params=None, *args, **kwargs):
        params = dict(params or {}, enable_file_urls=True, quiet=True, noprogress=True)
        super().__init__(params, *args, **kwargs)


def _file_url(path):
    return "file:///" + str(path).replace("\\", "/")


@unittest.skipUnless(FFMPEG.is_file(), "需要 backend/bin/ffmpeg.exe")
class ReportEachFinishedItemTest(unittest.TestCase):
    """使用者實測:頻道/播放清單網址下載到一半被 YouTube 限流,整個下載工作中止,
    已經下載完的那幾支的來源記錄和自動轉錄全部被丟掉,只能手動轉錄、變成「本機上傳」。
    每一支完成當下就要回報,不能等整個網址跑完才一次處理。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "測試音檔.wav"
        subprocess.run([str(FFMPEG), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(self.src)],
                       capture_output=True, check=True)
        self.patches = [
            mock.patch.object(downloader.yt_dlp, "YoutubeDL", _FileUrlYoutubeDL),
            mock.patch.object(downloader, "ARCHIVE_FILE", str(self.dir / "archive.txt")),  # 不碰真的 archive
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_reports_the_final_file_as_soon_as_it_is_in_place(self):
        reported = []

        def on_item_done(meta):
            reported.append((meta, os.path.exists(meta["path"])))

        out = self.dir / "downloads"
        result = downloader.download_media(_file_url(self.src), output_dir=str(out), format_type="audio",
                                           on_item_done=on_item_done)
        self.assertEqual(len(reported), 1)
        meta, existed_when_reported = reported[0]
        self.assertTrue(existed_when_reported)
        self.assertEqual(Path(meta["path"]).parent, out)
        self.assertEqual(Path(meta["path"]).suffix, ".mp3")   # 回報的是轉檔後的最終檔案
        self.assertEqual(meta["title"], "測試音檔")
        self.assertEqual([r["path"] for r in result], [meta["path"]])

    def test_a_failing_callback_does_not_break_the_download(self):
        def on_item_done(meta):
            raise RuntimeError("could not queue transcription")

        out = self.dir / "downloads"
        with mock.patch("traceback.print_exc"):
            result = downloader.download_media(_file_url(self.src), output_dir=str(out), format_type="audio",
                                               on_item_done=on_item_done)
        self.assertEqual(len(result), 1)
        self.assertTrue(os.path.exists(result[0]["path"]))

    def test_an_item_finished_before_a_later_failure_is_still_reported(self):
        # 模擬「同一個網址裡,前面的影片下載好了,後面的失敗」:直接用真的 yt-dlp 跑兩個網址,
        # 第二個不存在,確認第一個在失敗發生前就已經回報
        reported = []
        pp = downloader._ReportFinishedItem(lambda meta: reported.append(meta["path"]))
        opts = {"format": "bestaudio/best", "ffmpeg_location": str(FFMPEG.parent),
                "outtmpl": str(self.dir / "downloads" / "%(title)s.%(ext)s"),
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]}
        with downloader.yt_dlp.YoutubeDL(opts) as ydl:
            ydl.add_post_processor(pp, when="after_move")
            ydl.extract_info(_file_url(self.src), download=True)
            with self.assertRaises(Exception):
                ydl.extract_info(_file_url(self.dir / "missing.wav"), download=True)
        self.assertEqual(len(reported), 1)
        self.assertTrue(reported[0].endswith(".mp3"))


if __name__ == "__main__":
    unittest.main()
