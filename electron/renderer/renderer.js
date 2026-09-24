// 這支檔案由後端 server.py 當靜態檔案伺服出去,跟後端同一個 origin,
// 所以可以直接用相對路徑打 /api/... ,不會有跨來源問題。

// ---- 待處理清單:選檔案/加網址先停在這裡,按「開始處理」才真的送進後端佇列 ----
// 選項在「加入當下」就鎖進這一項裡(不是按開始處理時才統一套用目前的下拉選單值),
// 這樣先加一筆選 audio、再把下拉選單改成 video 才加第二筆,不會讓第一筆也被改成 video。
// 每一項: { kind: "download" | "transcribe", url?: string, path?: string, opts: {...} }
let pending = [];

function currentOptions() {
  return {
    format_type: document.getElementById("format-type").value,
    want_srt: document.getElementById("want-srt").checked,
    skip_existing: document.getElementById("skip-existing").checked,
  };
}

function describeOpts(opts) {
  const parts = [opts.format_type === "video" ? "影片" : "音訊"];
  if (opts.want_srt) parts.push("含字幕");
  if (opts.skip_existing) parts.push("已存在則跳過");
  return parts.join("、");
}

function renderPending() {
  const list = document.getElementById("pending-list");
  list.innerHTML = "";
  for (const item of pending) {
    const li = document.createElement("li");
    const what = item.kind === "download" ? `下載：${item.url}` : `轉錄：${item.path}`;
    li.textContent = `${what}（${describeOpts(item.opts)}）`;
    list.appendChild(li);
  }
  const hasItems = pending.length > 0;
  document.getElementById("start-btn").disabled = !hasItems;
  document.getElementById("clear-pending-btn").disabled = !hasItems;
}

// ---- 跟後端溝通 ----

async function fetchJobs() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  renderJobs(data.jobs);
}

function stateLabel(state) {
  const labels = {
    pending: "排隊中", running: "執行中", done: "完成", error: "失敗",
    "pending-retry": "待重試", cancelled: "已取消",
  };
  return labels[state] || state;
}

function renderJobs(jobs) {
  const tbody = document.querySelector("#jobs-table tbody");
  tbody.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    const percent = Math.round(job.progress || 0);
    const stateCell = `
      <span class="state-${job.state}">${stateLabel(job.state)}</span>
      ${job.message ? `<div style="font-size:12px;color:#666">${job.message}</div>` : ""}
      ${job.state === "running" ? `<div class="progress-bar"><div class="progress-fill" style="width:${percent}%"></div></div><div style="font-size:12px">${percent}%</div>` : ""}
    `;
    const actions = [];
    if (job.state === "running") {
      actions.push(`<button data-cancel="${job.id}">取消</button>`);
      if (job.type === "transcribe") {
        actions.push(`<button data-pause="${job.id}" data-paused="${job.paused ? "1" : "0"}">${job.paused ? "繼續" : "暫停"}</button>`);
      }
    }
    tr.innerHTML = `
      <td>${job.id}</td>
      <td>${job.type === "download" ? "下載" : "轉錄"}</td>
      <td>${job.content ?? ""}</td>
      <td>${stateCell}</td>
      <td>${job.error_message ?? ""}</td>
      <td>${actions.join(" ")}</td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => cancelJob(btn.dataset.cancel));
  });
  tbody.querySelectorAll("[data-pause]").forEach((btn) => {
    btn.addEventListener("click", () => pauseJob(btn.dataset.pause));
  });
}

async function startProcessing() {
  for (const item of pending) {
    if (item.kind === "download") {
      await fetch("/api/jobs/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: item.url, ...item.opts }),
      });
    } else {
      await fetch("/api/jobs/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path, want_srt: item.opts.want_srt, skip_existing: item.opts.skip_existing }),
      });
    }
  }
  pending = [];
  renderPending();
  fetchJobs();
}

async function retryPending() {
  await fetch("/api/jobs/retry", { method: "POST" });
  fetchJobs();
}

async function cancelJob(id) {
  await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
  fetchJobs();
}

async function pauseJob(id) {
  // 暫停後按鈕變成「繼續」,再按一次送 resume —— 用 dataset 記目前是哪個動作
  const btn = document.querySelector(`[data-pause="${id}"]`);
  const isPaused = btn.dataset.paused === "1";
  await fetch(`/api/jobs/${id}/${isPaused ? "resume" : "pause"}`, { method: "POST" });
  fetchJobs();
}

// ---- 使用者操作:先加進待處理清單,不會立刻送到後端 ----

document.getElementById("pick-files").addEventListener("click", async () => {
  // window.electronAPI 由 preload.js 透過 contextBridge 曝露,
  // 回傳的是使用者選到的檔案在硬碟上的「真實路徑」,不是上傳的位元組內容。
  const paths = await window.electronAPI.pickFiles();
  const opts = currentOptions();
  for (const p of paths) {
    pending.push({ kind: "transcribe", path: p, opts });
  }
  renderPending();
});

document.getElementById("add-url").addEventListener("click", () => {
  const input = document.getElementById("yt-url");
  if (input.value.trim()) {
    pending.push({ kind: "download", url: input.value.trim(), opts: currentOptions() });
    input.value = "";
    renderPending();
  }
});

document.getElementById("start-btn").addEventListener("click", startProcessing);
document.getElementById("clear-pending-btn").addEventListener("click", () => {
  pending = [];
  renderPending();
});
document.getElementById("retry-btn").addEventListener("click", retryPending);
document.getElementById("refresh-btn").addEventListener("click", fetchJobs);

renderPending();
fetchJobs();
setInterval(fetchJobs, 1500); // 簡單輪詢,先求能動,之後可以改成推播
