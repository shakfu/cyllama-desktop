const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cyllama", {
  getSidecarInfo: () => ipcRenderer.invoke("sidecar:info"),
  pickModel: () => ipcRenderer.invoke("dialog:pickModel"),
  pickAudio: () => ipcRenderer.invoke("dialog:pickAudio"),
  pickFolder: () => ipcRenderer.invoke("dialog:pickFolder"),
  settings: {
    get: () => ipcRenderer.invoke("settings:get"),
    set: (patch) => ipcRenderer.invoke("settings:set", patch),
  },
  restartSidecar: () => ipcRenderer.invoke("sidecar:restart"),
  openPreferences: () => ipcRenderer.invoke("prefs:open"),
  fileExists: (path) => ipcRenderer.invoke("fs:exists", path),
  revealItem: (path) => ipcRenderer.invoke("shell:revealItem", path),
  chats: {
    list:   () => ipcRenderer.invoke("chats:list"),
    load:   (id) => ipcRenderer.invoke("chats:load", id),
    save:   (chat) => ipcRenderer.invoke("chats:save", chat),
    delete: (id) => ipcRenderer.invoke("chats:delete", id),
  },
  log: {
    recent: () => ipcRenderer.invoke("log:recent"),
    // Returns an unsubscribe function. The wrapper hides the IPC
    // event object so the renderer only sees the entry payload.
    subscribe: (handler) => {
      const fn = (_e, entry) => handler(entry);
      ipcRenderer.on("sidecar:log", fn);
      return () => ipcRenderer.removeListener("sidecar:log", fn);
    },
  },
});
