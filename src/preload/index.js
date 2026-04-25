const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cyllama", {
  getSidecarInfo: () => ipcRenderer.invoke("sidecar:info"),
  pickModel: () => ipcRenderer.invoke("dialog:pickModel"),
  fileExists: (path) => ipcRenderer.invoke("fs:exists", path),
  chats: {
    list:   () => ipcRenderer.invoke("chats:list"),
    load:   (id) => ipcRenderer.invoke("chats:load", id),
    save:   (chat) => ipcRenderer.invoke("chats:save", chat),
    delete: (id) => ipcRenderer.invoke("chats:delete", id),
  },
});
