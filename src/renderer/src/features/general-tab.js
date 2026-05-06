// General tab. Replaces the old Settings overlay; surfaces /info data
// (cyllama version, backends, paths, devices) and a Preferences section
// where the user manages global settings (currently: extra read-only
// model directories scanned alongside MODELS_DIR).

import { getInfo, resetSidecarInfo } from "../lib/sidecar.js";

let cached = null;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtBackends(b) {
  if (!b || typeof b !== "object") return "(none reported)";
  const on = Object.entries(b).filter(([, v]) => v).map(([k]) => k);
  return on.length ? on.join(", ") : "(none enabled)";
}

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

// Renders the Preferences -> Model directories block. Read-only primary
// row + one row per pinned extra; an "Add directory..." button at the
// bottom. Edits stage in ``draft`` until the user clicks Apply, which
// persists via window.cyllama.settings.set + restarts the sidecar.
async function renderPreferences(host, info) {
  const pref = el("div", { class: "rt-section" });
  pref.appendChild(el("div", { class: "rt-section-head" }, el("h3", {}, "Preferences")));
  const sub = el("div", { class: "mt-subhead" }, "Model directories");
  pref.appendChild(sub);
  const status = el("div", { class: "gen-pref-status" });
  const list = el("div", { class: "gen-pref-list" });

  // Snapshot of what the sidecar is currently scanning. The primary
  // is read from /info (single source of truth, survives restart).
  const primary = info.sidecar?.models_dir || "";
  const draft = {
    extras: [...(info.sidecar?.models_extra || [])],
    dirty: false,
  };

  function row(label, path, opts = {}) {
    const r = el("div", { class: "gen-pref-row" });
    r.appendChild(el("div", { class: "gen-pref-row-label" }, label));
    r.appendChild(el("div", { class: "gen-pref-row-path mono" }, path || "(none)"));
    if (opts.removable) {
      const rm = el("button", {
        type: "button", class: "btn-mini",
        onclick: () => {
          draft.extras = draft.extras.filter((p) => p !== path);
          draft.dirty = true;
          rebuild();
        },
      }, "Remove");
      r.appendChild(rm);
    }
    return r;
  }

  function rebuild() {
    list.replaceChildren();
    list.appendChild(row("primary", primary));
    if (draft.extras.length === 0) {
      list.appendChild(el("div", { class: "gen-pref-empty" },
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
    type: "button", class: "btn primary",
    disabled: true,
    onclick: async () => {
      applyBtn.disabled = true;
      status.dataset.kind = "info";
      status.textContent = "saving...";
      try {
        await window.cyllama.settings.set({ models_extra: draft.extras });
        status.textContent = "restarting sidecar...";
        // The renderer caches /info; reset so the next caller picks
        // up the new models_extra advertised by the freshly-booted
        // sidecar.
        try { resetSidecarInfo(); } catch (_) {}
        await window.cyllama.restartSidecar();
        // Tell the chat hot path to drop its cached {port, token}
        // -- after restart those are fresh values and the old ones
        // route to a now-dead listener.
        try { window.dispatchEvent(new CustomEvent("sidecar:restarted")); } catch (_) {}
        // Repopulate /info so the primary row + extras reflect the
        // post-restart state, and re-fire the catalog refresh used
        // by other panes.
        cached = null;
        draft.dirty = false;
        try { window.dispatchEvent(new CustomEvent("models:cache-changed")); } catch (_) {}
        await refresh();
        status.dataset.kind = "ok";
        status.textContent = "saved · sidecar restarted";
      } catch (e) {
        status.dataset.kind = "err";
        status.textContent = `failed: ${e.message}`;
      }
    },
  }, "Apply");

  pref.appendChild(list);
  pref.appendChild(el("div", { class: "gen-pref-actions" }, addBtn, applyBtn));
  pref.appendChild(status);
  pref.appendChild(el("div", { class: "gen-pref-hint" },
    "The primary directory is the desktop's managed cache; drag-drop ",
    "imports and HuggingFace downloads land there. Extra directories ",
    "are scanned read-only -- ideal for an existing model library on ",
    "another disk. Changes take effect after the sidecar restarts."));

  rebuild();
  host.appendChild(pref);
}

async function refresh() {
  const host = document.getElementById("generalTabHost");
  if (!host) return;
  if (!cached) {
    try { cached = await getInfo(); }
    catch (e) { cached = { error: e.message }; }
  }
  const info = cached;
  const devices = Array.isArray(info.devices) ? info.devices : [];
  const devicesHtml = devices.length
    ? devices.map((d) => `
        <div class="rt-kv">
          <div class="k">${escapeHtml(d.type || "?")}</div>
          <div class="v">${escapeHtml(d.name || "")}<span class="rt-dev-desc">${escapeHtml(d.description ? " · " + d.description : "")}</span></div>
        </div>
      `).join("")
    : `<div class="rt-placeholder"><p>No devices reported. cyllama may not expose the probe in this build.</p></div>`;
  host.innerHTML = `
    <div class="rt-section">
      <div class="rt-section-head"><h3>About</h3></div>
      <div class="rt-kv">
        <div class="k">cyllama</div><div class="v" data-bind="version">-</div>
        <div class="k">backends</div><div class="v" data-bind="backends">-</div>
        <div class="k">models dir</div><div class="v mono" data-bind="modelsDir">-</div>
        <div class="k">artifacts dir</div><div class="v mono" data-bind="artifactsDir">-</div>
      </div>
    </div>
    <div class="rt-section">
      <div class="rt-section-head"><h3>Devices</h3></div>
      ${devicesHtml}
    </div>
  `;
  const setText = (k, v) => {
    const el = host.querySelector(`[data-bind=${k}]`);
    if (el) el.textContent = v;
  };
  if (info.error) {
    setText("version", `(error: ${info.error})`);
    return;
  }
  setText("version", info.cyllama?.version || "unknown");
  setText("backends", fmtBackends(info.backends));
  setText("modelsDir", info.sidecar?.models_dir || "");
  setText("artifactsDir", info.sidecar?.artifacts_dir || "");

  // Preferences is rendered last so the static panels stabilise first.
  await renderPreferences(host, info);
}

export function mount() {
  refresh();
}

export { refresh };
