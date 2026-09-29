"""偵測「一批工作全部跑完」,並回報這批實際成功的是哪幾種工作(下載 / 轉錄)。

舊版 3-4 每批做完會自動打開對應資料夾;這裡沒有「一批」的概念,只有一個個獨立的工作,
所以用狀態推:兩條佇列都空了、而且從上次回報之後至少有一件成功,就算一批跑完。
"待重試"(被 YouTube 限流)不算沒跑完——它們在等使用者按重試,不是在跑,所以已經拿到的
成果可以先讓使用者看到。重試後又被限流、沒有新成功的,不會再回報一次。
"""
import threading

from orchestrator import JobState


class BatchCompletion:
    def __init__(self, is_settled, on_complete):
        """is_settled(): 沒有任何工作在排隊或執行時回傳 True。
        on_complete(done_types): 一批跑完時呼叫一次,done_types 是這批成功的 JobType 集合。"""
        self._is_settled = is_settled
        self._on_complete = on_complete
        self._lock = threading.Lock()
        self._done_types = set()

    def on_job_terminal(self, job):
        """給 Orchestrator 的 on_job_terminal 用;兩條佇列的執行緒都會呼叫,所以要加鎖。"""
        with self._lock:
            if job.state == JobState.DONE:
                self._done_types.add(job.type)
            if not self._done_types or not self._is_settled():
                return
            done_types, self._done_types = self._done_types, set()
        self._on_complete(done_types)
