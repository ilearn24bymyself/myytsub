"""找不到 metadata 時(下載當下被限流、或本機挑選了本工具沒查到記錄的
檔案),用檔名反查 YouTube,盡量補回出處。

只有反查到的 video_id 真的出現在 download_archive.txt(下載當時確實
記錄過)才採信——這是回填舊資料時踩過的教訓:單靠標題文字搜尋比對,
偶爾會比對到完全不相關的影片,錯誤比對比「不知道出處」更糟。
"""
import os
from pathlib import Path

import yt_dlp

import download_record
from downloader import ARCHIVE_FILE


def _load_archive_ids() -> set:
    ids = set()
    if os.path.isfile(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 2:
                    ids.add(parts[1])
    return ids


def _flat_search(title: str) -> dict | None:
    """輕量搜尋(只掃搜尋結果列表,不深入抓取每支影片的完整頁面),
    避免觸發 YouTube 的 bot 偵測。"""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{title}", download=False)
    entries = info.get("entries") or []
    if not entries:
        return None
    e = entries[0]
    vid = e.get("id")
    return {
        "video_id": vid,
        "title": e.get("title"),
        "channel": e.get("channel") or e.get("uploader"),
        "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
    }


def guess_metadata(path: str) -> dict | None:
    title = Path(path).stem
    try:
        candidate = _flat_search(title)
    except Exception:
        return None
    if not candidate:
        return None
    if candidate["video_id"] not in _load_archive_ids():
        return None

    metadata = {
        "title": candidate["title"], "channel": candidate["channel"],
        "url": candidate["url"], "upload_date": None,
    }
    download_record.save([{"path": path, "video_id": candidate["video_id"], **metadata}])
    return metadata
