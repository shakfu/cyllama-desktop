// Preload for the Preferences window. Exposes a small surface
// scoped to what Settings actually needs:
//
//   - settings.{get,set}    -- shared with the main renderer; backed
//                              by <userData>/settings.json.
//   - getSidecarInfo        -- needed for the Sidecar / Models tabs to
//                              show the live /info payload + extra
//                              model dirs.
//   - restartSidecar        -- used after editing model directories.
//   - pickFolder            -- folder picker for adding model dirs.
//   - revealItem            -- reveal a path in the OS file manager.
//   - log.{recent,subscribe}-- sidecar stdout/stderr ring buffer for
//                              the Logs tab.
//
// We deliberately do NOT expose the main-renderer chat IPC (chats,
// pickModel, pickAudio, pickImage, etc.) -- the Preferences window
// has no business with chat state.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cyllama", {
  getSidecarInfo: () => ipcRenderer.invoke("sidecar:info"),
  restartSidecar: () => ipcRenderer.invoke("sidecar:restart"),
  pickFolder: () => ipcRenderer.invoke("dialog:pickFolder"),
  pickModel: () => ipcRenderer.invoke("dialog:pickModel"),
  revealItem: (path) => ipcRenderer.invoke("shell:revealItem", path),
  settings: {
    get: () => ipcRenderer.invoke("settings:get"),
    set: (patch) => ipcRenderer.invoke("settings:set", patch),
  },
  log: {
    recent: () => ipcRenderer.invoke("log:recent"),
    subscribe: (handler) => {
      const fn = (_e, entry) => handler(entry);
      ipcRenderer.on("sidecar:log", fn);
      return () => ipcRenderer.removeListener("sidecar:log", fn);
    },
  },
});
