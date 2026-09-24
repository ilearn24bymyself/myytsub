// 這支檔案由後端 server.py 當靜態檔案伺服出去,跟後端同一個 origin,
// 所以可以直接用相對路徑打 /api/... ,不會有跨來源問題。

async function fetchJobs() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  renderJobs(data.jobs);
}

function renderJobs(jobs) {
  const tbody = document.querySelector("#jobs-table tbody");
  tbody.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${job.id}</td>
      <td>${job.type}</td>
      <td>${job.payload}</td>
      <td class="state-${job.state}">${job.state}</td>
      <td>${job.error_message ?? ""}</td>
      <td>${job.state === "running" ? `<button data-cancel="${job.id}">取消</button>` : ""}</td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => cancelJob(btn.dataset.cancel));
  });
}

async function enqueueDownload(url) {
  await fetch("/api/jobs/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  fetchJobs();
}

async function enqueueTranscription(path) {
  await fetch("/api/jobs/transcribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
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

document.getElementById("pick-files").addEventListener("click", async () => {
  // window.electronAPI 由 preload.js 透過 contextBridge 曝露,
  // 回傳的是使用者選到的檔案在硬碟上的「真實路徑」,不是上傳的位元組內容。
  const paths = await window.electronAPI.pickFiles();
  for (const p of paths) {
    await enqueueTranscription(p);
  }
});

document.getElementById("add-url").addEventListener("click", () => {
  const input = document.getElementById("yt-url");
  if (input.value.trim()) {
    enqueueDownload(input.value.trim());
    input.value = "";
  }
});

document.getElementById("retry-btn").addEventListener("click", retryPending);
document.getElementById("refresh-btn").addEventListener("click", fetchJobs);

fetchJobs();
setInterval(fetchJobs, 2000); // 簡單輪詢,先求能動,之後可以改成推播
