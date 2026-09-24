"""背景任務協調器:取代舊專案 runner.py 的單一狀態鎖。

兩條各自獨立的線(lane):
- download lane:YouTube 下載,同一時間只跑一項(避免同時打太多請求給 YouTube)
- transcribe lane:本機檔案 / 已下載檔案的轉錄,同一時間只跑一項

兩條 lane 彼此並行,不會互相卡住(問題 3 的修正)。
"""
import enum
import itertools
import queue
import threading


class JobState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PENDING_RETRY = "pending-retry"
    CANCELLED = "cancelled"

    def __str__(self):  # 方便印出時直接顯示值,不顯示 JobState.DONE 這種形式
        return self.value


class JobType(str, enum.Enum):
    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"


class RateLimited(Exception):
    """downloader.py 偵測到限流/IP 鎖定訊號時拋出這個,跟一般下載錯誤區分開來。"""


class Job:
    def __init__(self, job_id, job_type, payload):
        self.id = job_id
        self.type = job_type
        self.payload = payload
        self.state = JobState.PENDING
        self.error_message = None
        self.progress = 0.0
        self.message = None
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()  # 預設「沒被暫停」;清掉這個旗標才是真的暫停
        self.paused = False  # 給畫面看的:目前是不是處於「使用者按了暫停」狀態


class Orchestrator:
    def __init__(self, download_fn, transcribe_fn, on_job_terminal=None):
        """
        download_fn(payload, cancel_event, pause_event, report_progress) /
        transcribe_fn(payload, cancel_event, pause_event, report_progress):
            真正執行下載/轉錄的函式;完成回傳、失敗丟例外、限流丟 RateLimited。
            pause_event:清掉代表暫停,fn 要自己去 wait() 才會真的停下來
            (目前只有轉錄的實作會用;下載不支援暫停,可以忽略這個參數)。
            report_progress(percent, message=None):執行過程中回報目前進度。
        on_job_terminal(job):
            每個工作進入終態(done/error/pending-retry/cancelled)時呼叫一次,
            用來觸發當日 index 增量重建。
        """
        self.download_fn = download_fn
        self.transcribe_fn = transcribe_fn
        self.on_job_terminal = on_job_terminal

        self._lock = threading.Lock()
        self._jobs = {}
        self._id_counter = itertools.count(1)

        self._download_queue = queue.Queue()
        self._transcribe_queue = queue.Queue()
        self._rate_limited = False  # 一旦被限流,後續下載項目直接跳過,不嘗試

        self._download_thread = threading.Thread(target=self._worker, args=(self._download_queue, self.download_fn), daemon=True)
        self._transcribe_thread = threading.Thread(target=self._worker, args=(self._transcribe_queue, self.transcribe_fn), daemon=True)
        self._download_thread.start()
        self._transcribe_thread.start()

    def _new_job(self, job_type, payload):
        job_id = str(next(self._id_counter))
        job = Job(job_id, job_type, payload)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def enqueue_download(self, payload):
        """payload 是不透明的:orchestrator 不理解它的形狀,原樣轉交給 download_fn。
        目前呼叫端(server.py)傳的是 {"url", "format_type", "want_srt", "skip_existing"}。"""
        job = self._new_job(JobType.DOWNLOAD, payload)
        self._download_queue.put(job)
        return job.id

    def enqueue_transcription(self, payload):
        """同上;呼叫端目前傳的是 {"path", "want_srt", "skip_existing"}。"""
        job = self._new_job(JobType.TRANSCRIBE, payload)
        self._transcribe_queue.put(job)
        return job.id

    def retry_pending(self):
        """使用者主動觸發:清除限流旗標,把所有 pending-retry 的下載工作重新排隊。"""
        with self._lock:
            self._rate_limited = False
            to_retry = [j for j in self._jobs.values() if j.type == JobType.DOWNLOAD and j.state == JobState.PENDING_RETRY]
        for job in to_retry:
            job.state = JobState.PENDING
            self._download_queue.put(job)

    def get_job(self, job_id):
        with self._lock:
            return self._jobs[job_id]

    def list_jobs(self):
        with self._lock:
            return list(self._jobs.values())  # dict 保留插入順序

    def cancel(self, job_id):
        """通知一個正在執行(或還在排隊)的工作取消。真正執行的 download_fn/transcribe_fn
        要自己去看 cancel_event 並盡快返回,協調器本身不會強行中斷執行緒。"""
        job = self.get_job(job_id)
        job.cancel_event.set()

    def pause(self, job_id):
        """通知一個正在執行的工作暫停。只有會去檢查 pause_event 的工作函式
        (目前是轉錄)才會真的停下來;下載目前不支援暫停,呼叫了也不會有效果。"""
        job = self.get_job(job_id)
        job.pause_event.clear()
        job.paused = True

    def resume(self, job_id):
        job = self.get_job(job_id)
        job.pause_event.set()
        job.paused = False

    def _finish(self, job, state, error_message=None):
        job.state = state
        job.error_message = error_message
        job.paused = False  # 工作結束了,不該還顯示「暫停中」
        if self.on_job_terminal:
            self.on_job_terminal(job)

    def _worker(self, q, fn):
        is_download_lane = q is self._download_queue
        while True:
            job = q.get()
            if job is None:  # shutdown 訊號
                q.task_done()
                return

            with self._lock:
                skip_as_rate_limited = is_download_lane and self._rate_limited
            if skip_as_rate_limited:
                # 還在被限流的狀態下:不嘗試,直接標記待重試(逐項嘗試只會讓鎖定拖更久)
                self._finish(job, JobState.PENDING_RETRY)
                q.task_done()
                continue

            job.state = JobState.RUNNING

            def report_progress(percent, message=None, _job=job):
                _job.progress = percent
                if message is not None:
                    _job.message = message

            try:
                fn(job.payload, job.cancel_event, job.pause_event, report_progress)
            except RateLimited:
                if is_download_lane:
                    with self._lock:
                        self._rate_limited = True
                self._finish(job, JobState.PENDING_RETRY)
            except Exception as e:
                self._finish(job, JobState.ERROR, str(e))
            else:
                if job.cancel_event.is_set():
                    self._finish(job, JobState.CANCELLED)
                else:
                    self._finish(job, JobState.DONE)
            q.task_done()

    def wait_idle(self):
        """等到兩條 lane 目前排的工作都跑完(測試用,避免用 sleep 猜時間)。"""
        self._download_queue.join()
        self._transcribe_queue.join()

    def shutdown(self):
        self._download_queue.put(None)
        self._transcribe_queue.put(None)
        self._download_thread.join(timeout=5)
        self._transcribe_thread.join(timeout=5)
