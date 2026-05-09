// Preferences window renderer. Independent of the main renderer:
// loads its own bundle, has its own preload, owns its own state.
// Communicates with the same Electron main process so shared
// resources (settings.json, sidecar /info, sidecar stdout ring
// buffer) stay consistent across the two windows.
//
// Three populated tabs:
//
//   Models   -- read/write of the model directories list. Edits
//               persist via window.cyllama.settings.set + restart
//               the sidecar so the new scan roots take effect.
//   Sidecar  -- read-only view of /info: cyllama version, backends,
//               devices, paths.
//   Logs     -- live tail of the sidecar's stdout/stderr ring
//               buffer (same source as the main window's Console
//               drawer). Auto-scrolls when near the bottom.
//
// General is intentionally a placeholder for now; theme / default
// sampling preset / window behavior land here in follow-ups.

import * as serverPane from "../renderer/src/features/server-pane.js";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function basename(p) { return p ? p.split(/[\\/]/).pop() : ""; }

// --- Tab switcher ----------------------------------------------------------

let activeTab = "general";
function setTab(name) {
  if (name === activeTab) return;
  activeTab = name;
  for (const btn of document.querySelectorAll(".prefs-nav-item")) {
    btn.classList.toggle("active", btn.dataset.prefsTab === name);
  }
  for (const pane of document.querySelectorAll(".prefs-pane")) {
    const match = pane.dataset.prefsPane === name;
    pane.hidden = !match;
    pane.classList.toggle("active", match);
  }
}
for (const btn of document.querySelectorAll(".prefs-nav-item")) {
  btn.addEventListener("click", () => setTab(btn.dataset.prefsTab));
}

// --- Models tab: model directories ----------------------------------------

let prefsCache = { info: null };

async function refreshInfoCache() {
  try { prefsCache.info = await window.cyllama.getSidecarInfo(); }
  catch { prefsCache.info = null; }
  // The Preferences window opens against an already-running sidecar;
  // /info is fetched directly via the bearer token returned by
  // ``sidecar:info``. We hit the loopback URL once and stash it.
  if (prefsCache.info) {
    try {
      const r = await fetch(`http://127.0.0.1:${prefsCache.info.port}/info`, {
        headers: { authorization: `Bearer ${prefsCache.info.token}` },
      });
      if (r.ok) prefsCache.fullInfo = await r.json();
    } catch { /* network blip; tabs degrade gracefully */ }
  }
}

async function renderModelsTab() {
  const host = document.getElementById("prefsModels");
  if (!host) return;
  host.replaceChildren();

  const settings = await window.cyllama.settings.get();
  const primary = prefsCache.fullInfo?.sidecar?.models_dir || "";
  const draft = {
    extras: [...(settings.models_extra || [])],
    dirty: false,
  };

  host.appendChild(el("p", { class: "prefs-hint" },
    "The primary directory is the desktop's managed cache; drag-drop ",
    "imports and HuggingFace downloads land there. Extra directories ",
    "are scanned read-only and ideal for an existing model library on ",
    "another disk. Changes take effect after the sidecar restarts."));

  const list = el("div", { class: "prefs-rows" });
  const status = el("div", { class: "prefs-status" });

  function row(label, path, opts = {}) {
    const r = el("div", { class: "prefs-row" });
    r.appendChild(el("div", { class: "prefs-row-label" }, label));
    r.appendChild(el("div", { class: "prefs-row-path mono" }, path || "(none)"));
    if (opts.removable) {
      r.appendChild(el("button", {
        type: "button", class: "btn-mini",
        onclick: () => {
          draft.extras = draft.extras.filter((p) => p !== path);
          draft.dirty = true;
          rebuild();
        },
      }, "Remove"));
    }
    return r;
  }

  function rebuild() {
    list.replaceChildren();
    list.appendChild(row("primary", primary));
    if (draft.extras.length === 0) {
      list.appendChild(el("div", { class: "prefs-empty-row" },
        "No extra directories. Add one to scan a library outside the app's models cache."));
    } else {
      for (const p of draft.extras) list.appendChild(row("extra", p, { removable: true }));
    }
    applyBtn.disabled = !draft.dirty;
  }

  const addBtn = el("button", {
    type: "button", class: "btn",
    onclick: async () => {
      const picked = await window.cyllama.pickFolder();
      if (!picked) return;
      if (picked === primary || draft.extras.includes(picked)) {
        status.textContent = "directory already in the list";
        status.dataset.kind = "info";
        return;
      }
      draft.extras.push(picked);
      draft.dirty = true;
      status.textContent = "";
      rebuild();
    },
  }, "Add directory...");

  const applyBtn = el("button", {
    type: "button", class: "btn primary", disabled: true,
    onclick: async () => {
      applyBtn.disabled = true;
      status.dataset.kind = "info";
      status.textContent = "saving...";
      try {
        await window.cyllama.settings.set({ models_extra: draft.extras });
        status.textContent = "restarting sidecar...";
        await window.cyllama.restartSidecar();
        // Refresh the cached /info so primary path + extras reflect
        // the post-restart state.
        await refreshInfoCache();
        draft.dirty = false;
        status.dataset.kind = "ok";
        status.textContent = "saved · sidecar restarted";
      } catch (e) {
        status.dataset.kind = "err";
        status.textContent = `failed: ${e.message}`;
      }
    },
  }, "Apply");

  host.appendChild(list);
  host.appendChild(el("div", { class: "prefs-actions" }, addBtn, applyBtn));
  host.appendChild(status);
  rebuild();
}

// --- Sidecar tab: read-only /info view -----------------------------------

function fmtBackends(b) {
  if (!b || typeof b !== "object") return "(none reported)";
  const on = Object.entries(b).filter(([, v]) => v).map(([k]) => k);
  return on.length ? on.join(", ") : "(none enabled)";
}

function renderSidecarTab() {
  const host = document.getElementById("prefsSidecar");
  if (!host) return;
  host.replaceChildren();
  const info = prefsCache.fullInfo;
  if (!info) {
    host.appendChild(el("p", { class: "prefs-empty" }, "Could not reach the sidecar."));
    return;
  }

  // About: cyllama version + backends.
  host.appendChild(el("h3", { class: "prefs-section" }, "About"));
  host.appendChild(el("div", { class: "prefs-kv" },
    el("div", { class: "k" }, "cyllama"),
    el("div", { class: "v" }, info.cyllama?.version || "unknown"),
    el("div", { class: "k" }, "backends"),
    el("div", { class: "v" }, fmtBackends(info.backends)),
  ));

  // Paths.
  const sc = info.sidecar || {};
  host.appendChild(el("h3", { class: "prefs-section" }, "Paths"));
  const paths = el("div", { class: "prefs-kv" });
  for (const [k, label] of [
    ["models_dir", "models"],
    ["artifacts_dir", "artifacts"],
    ["rag_dir", "rag"],
    ["uploads_dir", "uploads"],
  ]) {
    const v = sc[k];
    if (!v) continue;
    paths.appendChild(el("div", { class: "k" }, label));
    paths.appendChild(el("div", { class: "v mono" },
      el("button", {
        type: "button", class: "prefs-link",
        title: "Reveal in file manager",
        onclick: () => window.cyllama.revealItem && window.cyllama.revealItem(v),
      }, v),
    ));
  }
  host.appendChild(paths);

  // Devices.
  const devices = Array.isArray(info.devices) ? info.devices : [];
  host.appendChild(el("h3", { class: "prefs-section" }, "Devices"));
  if (!devices.length) {
    host.appendChild(el("div", { class: "prefs-empty-row" },
      "No devices reported. cyllama may not expose the probe in this build."));
  } else {
    const dev = el("div", { class: "prefs-kv" });
    for (const d of devices) {
      dev.appendChild(el("div", { class: "k" }, d.type || "?"));
      dev.appendChild(el("div", { class: "v" },
        d.name || "",
        d.description ? ` · ${d.description}` : "",
      ));
    }
    host.appendChild(dev);
  }

  // Server kinds + features (informational).
  const features = info.features || {};
  const enabled = Object.entries(features).filter(([, v]) => v).map(([k]) => k);
  host.appendChild(el("h3", { class: "prefs-section" }, "Capabilities"));
  host.appendChild(el("div", { class: "prefs-kv" },
    el("div", { class: "k" }, "features"),
    el("div", { class: "v" }, enabled.length ? enabled.join(", ") : "(none)"),
    el("div", { class: "k" }, "server kinds"),
    el("div", { class: "v" }, (info.server_kinds || []).join(", ") || "(none)"),
  ));
}

// --- Logs tab: live sidecar stdout/stderr tail ----------------------------

let logsLoaded = false;

function fmtTime(t) {
  return new Date(t).toTimeString().slice(0, 8);
}

function appendLogLine(entry) {
  const host = document.getElementById("prefsLogs");
  if (!host) return;
  const empty = host.querySelector(".prefs-logs-empty");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = "prefs-log-line" + (entry.s === "err" ? " err" : "");
  const ts = document.createElement("span");
  ts.className = "ts";
  ts.textContent = fmtTime(entry.t);
  div.appendChild(ts);
  div.appendChild(document.createTextNode(entry.line));
  host.appendChild(div);
  while (host.childElementCount > 2000) host.firstChild.remove();
  const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 40;
  if (nearBottom) host.scrollTop = host.scrollHeight;
}

async function renderLogsTab() {
  const host = document.getElementById("prefsLogs");
  if (!host || logsLoaded) return;
  logsLoaded = true;
  try {
    const recent = await window.cyllama.log.recent();
    if (!recent || recent.length === 0) {
      host.appendChild(el("div", { class: "prefs-logs-empty" }, "(no output yet)"));
    } else {
      for (const entry of recent) appendLogLine(entry);
    }
  } catch (e) {
    host.appendChild(el("div", { class: "prefs-logs-empty" }, `load failed: ${e.message}`));
  }
  host.scrollTop = host.scrollHeight;
}

// --- Wiring ---------------------------------------------------------------

// Mount the server controls into the Sidecar tab. The server-pane
// module (originally a left-sidebar view in the main renderer) now
// lives here because starting/stopping a long-running OpenAI-compat
// server is a configuration concern, not a per-chat tool.
async function mountServerSection() {
  const features = prefsCache.fullInfo?.features || {};
  const host = document.getElementById("prefsServer");
  if (!host) return;
  if (!features.openai_server) {
    host.replaceChildren();
    host.appendChild(el("div", { class: "prefs-empty-row" },
      "This sidecar build does not expose the OpenAI-compatible server."));
    return;
  }
  await serverPane.show(host);
}

(async () => {
  await refreshInfoCache();
  await renderModelsTab();
  renderSidecarTab();
  await mountServerSection();

  // Subscribe to live log lines unconditionally so the tail is
  // current the moment the user clicks Logs (no stale dump on
  // first show).
  if (window.cyllama?.log?.subscribe) {
    window.cyllama.log.subscribe(appendLogLine);
  }

  const clearBtn = document.getElementById("prefsLogsClear");
  if (clearBtn) clearBtn.addEventListener("click", () => {
    const host = document.getElementById("prefsLogs");
    if (host) host.replaceChildren();
  });

  // Lazy-fetch the recent log dump on first Logs click; otherwise
  // we'd hit the IPC unnecessarily for users who never visit the tab.
  for (const btn of document.querySelectorAll('.prefs-nav-item[data-prefs-tab="logs"]')) {
    btn.addEventListener("click", () => { renderLogsTab(); });
  }
})();
