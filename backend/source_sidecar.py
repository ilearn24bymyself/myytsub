"""影片旁的來源資訊檔:「影片名稱.source.json」。

每支影片下載完的當下就寫好。影片搬到哪(換資料夾、換電腦、ZIP 版另開資料夾),
資訊就跟到哪;手動挑檔轉錄時先看同一個資料夾有沒有這個檔——這是使用者的想法,
用來取代「只存在程式資料夾、以完整路徑當索引」的 download_record(影片一搬家就對不上)。
"""
import json
from pathlib import Path

_FIELDS = ("title", "channel", "url", "upload_date")


def sidecar_path(media_path) -> Path:
    p = Path(media_path)
    return p.with_name(p.stem + ".source.json")


def write(media_path, metadata: dict) -> None:
    """只寫得出處的資訊;沒有網址的寫了沒用,反而會擋掉之後的查找。"""
    if not metadata or not metadata.get("url"):
        return
    data = {k: metadata.get(k) for k in _FIELDS}
    sidecar_path(media_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read(media_path) -> dict | None:
    try:
        data = json.loads(sidecar_path(media_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("url"):
        return None
    return {k: data.get(k) for k in _FIELDS}
