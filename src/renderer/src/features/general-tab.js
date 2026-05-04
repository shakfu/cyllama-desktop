// General tab. Replaces the old Settings overlay; surfaces /info data
// (cyllama version, backends, paths) plus a placeholder section for
// future preferences (theme, default sampling, model directory).

import { getInfo } from "../lib/sidecar.js";

let cached = null;

function fmtBackends(b) {
  if (!b || typeof b !== "object") return "(none reported)";
  const on = Object.entries(b).filter(([, v]) => v).map(([k]) => k);
  return on.length ? on.join(", ") : "(none enabled)";
}

async function refresh() {
  const host = document.getElementById("generalTabHost");
  if (!host) return;
  if (!cached) {
    try { cached = await getInfo(); }
    catch (e) { cached = { error: e.message }; }
  }
  const info = cached;
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
      <div class="rt-section-head"><h3>Preferences</h3></div>
      <div class="rt-placeholder">
        <p>Theme, default sampling, default models directory. Coming in a later phase.</p>
      </div>
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
}

export function mount() {
  refresh();
}

export { refresh };
