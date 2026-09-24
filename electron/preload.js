const { contextBridge, ipcRenderer } = require("electron");

// contextIsolation 開著,渲染畫面不能直接碰 Node/Electron API,
// 只能透過這裡刻意曝露出去的最小介面。
contextBridge.exposeInMainWorld("electronAPI", {
  pickFiles: () => ipcRenderer.invoke("pick-files"),
});
