"""持久化「下載檔案路徑 → 影片 metadata」的對應記錄。

下載完成的當下,metadata 就在 job payload 裡可以直接帶給轉錄工作用;但使用者
之後也可能透過「選擇本機影音檔」手動挑同一份檔案來轉錄(例如下載中斷分好幾次、
或只是想重新產生逐字稿)。這種情況下 payload 裡沒有 metadata,查這份記錄才能
補回出處,不然只能誠實顯示「本機上傳」。
"""
import json
import os

_RECORD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_metadata.json")


def _load() -> dict:
    if not os.path.isfile(_RECORD_FILE):
        return {}
    try:
        with open(_RECORD_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save(entries: list) -> None:
    """entries 是 download_media() 回傳的清單,每筆至少含 path,其餘 metadata
    欄位缺的就是 None(例如 WinError 重試救回來的檔案),原樣存進去即可。"""
    record = _load()
    for e in entries:
        record[os.path.abspath(e["path"])] = {
            "title": e.get("title"),
            "channel": e.get("channel"),
            "url": e.get("url"),
            "upload_date": e.get("upload_date"),
        }
    with open(_RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def lookup(path: str) -> dict | None:
    return _load().get(os.path.abspath(path))


def find_path_by_video_id(video_id: str) -> str | None:
    """反查:給一個 YouTube video ID,找回記錄裡對應的檔案路徑(如果有的話)。
    記錄本身沒存 video_id 這個欄位(url 裡就有,不重複存),用 video_id 是不是
    某筆記錄 url 的子字串來比對。"""
    for path, meta in _load().items():
        url = meta.get("url")
        if url and video_id in url:
            return path
    return None


def find_path_by_title(root_dir: str, title: str) -> str | None:
    """download_metadata.json 沒記錄到的舊資料(這個記錄機制上線前下載的檔案)
    只能靠標題比對實際磁碟上的檔名——yt-dlp 的 outtmpl 是 %(title)s.%(ext)s,
    檔名幾乎一定就是標題本身。掃 <root_dir>/<日期資料夾>/downloads 或 /uploads。"""
    if not os.path.isdir(root_dir):
        return None
    for day in os.listdir(root_dir):
        day_path = os.path.join(root_dir, day)
        if not os.path.isdir(day_path):
            continue
        for sub in ("downloads", "uploads"):
            sub_dir = os.path.join(day_path, sub)
            if not os.path.isdir(sub_dir):
                continue
            for f in os.listdir(sub_dir):
                stem, _ext = os.path.splitext(f)
                if stem == title:
                    return os.path.join(sub_dir, f)
    return None
