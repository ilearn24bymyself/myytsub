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
