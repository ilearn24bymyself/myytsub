"""找不到 metadata 時(下載當下被限流、或本機挑選了本工具沒查到記錄的
檔案),用檔名反查 YouTube,盡量補回出處。

採信條件:候選影片的標題,套用跟存檔時一樣的換字規則(yt_dlp 的
sanitize_filename)後,必須跟檔名「完全相同」。錯誤比對比「不知道出處」更糟,
所以寧可放棄也不猜。

早期版本改用「video_id 在 download_archive.txt 裡」當判斷,使用者實測後發現
不可靠:整個系列都下載過時,搜到相鄰的另一集也會在 archive 裡。archive 現在
只用來在「好幾支影片同名」時挑出本工具下載過的那一支。
"""
import os
import re
from pathlib import Path

import yt_dlp
from yt_dlp.utils import sanitize_filename

import download_record
from downloader import ARCHIVE_FILE

# 存檔時 yt-dlp 會把檔名不能用的字換成長得很像的字(例如 "/" -> "⧸")。拿檔名去搜尋
# 要換回來,否則搜不到——使用者實測的「7⧸25盈利3R」原樣搜尋回傳 0 筆。
# 只換這兩個:它們幾乎不會出現在正常標題裡;"｜"、"："這類全形字本來就常見於中文標題
_UNSANITIZE = {"⧸": "/", "⧹": "\\"}
_SEARCH_RESULTS = 5


def _search_query(stem: str) -> str:
    for replaced, original in _UNSANITIZE.items():
        stem = stem.replace(replaced, original)
    # YouTube 搜尋把「開頭或空白後的 -字」當成「排除這個字」。使用者實測的「ETH -3R」
    # 等於叫它找不含 3R 的結果,永遠 0 筆。只影響搜尋字串,比對仍然用完整標題
    return re.sub(r"(^|\s)-+(?=\S)", r"\1", stem)


def _load_archive_ids() -> set:
    ids = set()
    if os.path.isfile(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 2:
                    ids.add(parts[1])
    return ids


def _search_candidates(query: str) -> list:
    """輕量搜尋(只掃搜尋結果列表,不深入抓取每支影片的完整頁面),
    避免觸發 YouTube 的 bot 偵測。"""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{_SEARCH_RESULTS}:{query}", download=False)
    candidates = []
    for e in info.get("entries") or []:
        vid = e.get("id")
        candidates.append({
            "video_id": vid,
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
        })
    return candidates


def _pick_exact_match(stem: str, candidates: list):
    exact = [c for c in candidates if c.get("title") and sanitize_filename(c["title"]) == stem]
    if len({c["video_id"] for c in exact}) == 1:
        return exact[0]
    if not exact:
        return None
    # 好幾支不同影片同名(例如重新上傳):只有其中一支是本工具下載過的才採信
    archive = _load_archive_ids()
    in_archive = [c for c in exact if c["video_id"] in archive]
    return in_archive[0] if len({c["video_id"] for c in in_archive}) == 1 else None


def guess_metadata(path: str) -> dict | None:
    stem = Path(path).stem
    try:
        candidate = _pick_exact_match(stem, _search_candidates(_search_query(stem)))
    except Exception:
        return None
    if not candidate:
        return None

    metadata = {
        "title": candidate["title"], "channel": candidate["channel"],
        "url": candidate["url"], "upload_date": None,
    }
    # download_record.save() 只存 title/channel/url/upload_date,不留 video_id
    # (lookup() 從來不需要它),這裡就不傳,避免看起來像有存實際上被丟掉。
    download_record.save([{"path": path, **metadata}])
    return metadata
