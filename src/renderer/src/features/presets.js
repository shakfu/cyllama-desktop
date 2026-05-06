// Presets: named bundles of (sampling params + system prompt). Selecting
// a preset writes its values into the existing #p-* inputs and fires
// their input events so loadParams / saveParams stay in sync without a
// separate codepath. Built-ins are seeded read-only; user creations
// persist in localStorage and can be deleted.
//
// Bundles cover sampling keys + system prompt only. Hardware fields
// (n_gpu_layers, n_ctx, n_batch, main_gpu, split_mode, tensor_split)
// are deliberately omitted so switching a preset doesn't clobber the
// user's hardware setup -- those live with the machine, not the
// conversation style. captureCurrent() honours this by walking only
// SAMPLING_KEYS rather than all PARAM_KEYS.

const STORAGE_KEY = "presets_v1";

// Keys a preset is allowed to touch. Hardware fields are intentionally
// excluded -- see header comment.
const SAMPLING_KEYS = new Set([
  "temperature", "top_p", "top_k", "min_p", "repeat_penalty",
  "presence_penalty", "frequency_penalty",
  "mirostat", "mirostat_tau", "mirostat_eta",
  "max_tokens", "seed", "stop_sequences",
]);

const BUILTINS = {
  Default: {
    builtin: true,
    params: {
      temperature: 0.8, top_p: 0.95, top_k: 40, min_p: 0.05,
      repeat_penalty: 1.0, presence_penalty: 0, frequency_penalty: 0,
      mirostat: 0, mirostat_tau: 5.0, mirostat_eta: 0.1,
      max_tokens: 512, seed: "", stop_sequences: "",
    },
    system_prompt: "",
  },
  Creative: {
    builtin: true,
    params: {
      temperature: 1.1, top_p: 0.97, top_k: 80, min_p: 0.02,
      repeat_penalty: 1.05, presence_penalty: 0.3, frequency_penalty: 0.2,
      mirostat: 0, mirostat_tau: 5.0, mirostat_eta: 0.1,
      max_tokens: 1024, seed: "", stop_sequences: "",
    },
    system_prompt: "",
  },
  Precise: {
    builtin: true,
    params: {
      temperature: 0.2, top_p: 0.85, top_k: 20, min_p: 0.1,
      repeat_penalty: 1.05, presence_penalty: 0, frequency_penalty: 0,
      mirostat: 0, mirostat_tau: 5.0, mirostat_eta: 0.1,
      max_tokens: 512, seed: "", stop_sequences: "",
    },
    system_prompt: "",
  },
  Code: {
    builtin: true,
    params: {
      temperature: 0.2, top_p: 0.95, top_k: 40, min_p: 0.05,
      repeat_penalty: 1.0, presence_penalty: 0, frequency_penalty: 0,
      mirostat: 0, mirostat_tau: 5.0, mirostat_eta: 0.1,
      max_tokens: 2048, seed: "", stop_sequences: "",
    },
    system_prompt: "You are a precise coding assistant. Prefer correct, minimal code. When showing code, use fenced blocks with the language tag.",
  },
  "Long-context": {
    builtin: true,
    params: {
      temperature: 0.5, top_p: 0.9, top_k: 40, min_p: 0.05,
      repeat_penalty: 1.0, presence_penalty: 0, frequency_penalty: 0,
      mirostat: 0, mirostat_tau: 5.0, mirostat_eta: 0.1,
      max_tokens: 4096, seed: "", stop_sequences: "",
    },
    system_prompt: "",
  },
};

let host = null;
let select = null;
let saveBtn = null;
let deleteBtn = null;

let bridge = null; // { paramKeys, paramEl, paramOut, formatParamValue, getSystemPrompt, setSystemPromptUI }

function loadUserPresets() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return (parsed && typeof parsed === "object") ? parsed : {};
  } catch { return {}; }
}

function saveUserPresets(map) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(map)); } catch {}
}

function allPresets() {
  // Built-ins are immutable seeds; user presets shadow them by name.
  return { ...BUILTINS, ...loadUserPresets() };
}

function captureCurrent() {
  const params = {};
  for (const key of bridge.paramKeys) {
    if (!SAMPLING_KEYS.has(key)) continue;
    const el = bridge.paramEl(key);
    if (!el) continue;
    params[key] = el.value;
  }
  return {
    params,
    system_prompt: bridge.getSystemPrompt() || "",
  };
}

function applyPreset(name) {
  const all = allPresets();
  const preset = all[name];
  if (!preset) return;
  for (const [k, v] of Object.entries(preset.params || {})) {
    // Preset bundles only touch sampling fields. Reject anything else
    // (forwards-compat: an old bundle with hardware keys can't clobber
    // the current hardware setup when applied).
    if (!SAMPLING_KEYS.has(k)) continue;
    const el = bridge.paramEl(k);
    if (!el) continue;
    el.value = String(v ?? "");
    const out = bridge.paramOut(k);
    if (out) out.textContent = bridge.formatParamValue(k, el.value);
    // Fire input so any side-effects (saveParams, mirostat visibility) run.
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (typeof preset.system_prompt === "string") {
    bridge.setSystemPromptUI(preset.system_prompt);
    const sysEl = document.getElementById("p-system_prompt");
    if (sysEl) sysEl.dispatchEvent(new Event("input", { bubbles: true }));
  }
  // Persist active preset name so the dropdown lands on it next launch.
  try { localStorage.setItem("presets_v1_active", name); } catch {}
  refreshButtons(name);
}

function refreshButtons(activeName) {
  const all = allPresets();
  const isBuiltin = !!(all[activeName] && all[activeName].builtin);
  if (deleteBtn) deleteBtn.disabled = isBuiltin;
}

function rebuildSelect() {
  if (!select) return;
  const prev = select.value;
  while (select.firstChild) select.removeChild(select.firstChild);

  const builtinGroup = document.createElement("optgroup");
  builtinGroup.label = "Built-in";
  for (const name of Object.keys(BUILTINS)) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name;
    builtinGroup.appendChild(o);
  }
  select.appendChild(builtinGroup);

  const userMap = loadUserPresets();
  const userKeys = Object.keys(userMap);
  if (userKeys.length) {
    const userGroup = document.createElement("optgroup");
    userGroup.label = "Saved";
    for (const name of userKeys) {
      const o = document.createElement("option");
      o.value = name; o.textContent = name;
      userGroup.appendChild(o);
    }
    select.appendChild(userGroup);
  }

  // Restore selection if still valid.
  let active = prev;
  if (!active || ![...select.options].some((o) => o.value === active)) {
    try { active = localStorage.getItem("presets_v1_active") || "Default"; }
    catch { active = "Default"; }
  }
  if ([...select.options].some((o) => o.value === active)) select.value = active;
  refreshButtons(active);
}

function build() {
  // Mount the presets bar into the dedicated "Preset" section at the
  // top of the Parameters tab (LMStudio-style IA: preset is a chat-
  // start choice, not a sub-control of Sampling). Fall back to the
  // Sampling section if a future layout drops the dedicated Preset
  // header so the bar still surfaces somewhere coherent.
  const headers = document.querySelectorAll(".rt-section-head h3");
  let target = null;
  for (const h of headers) {
    const t = (h.textContent || "").trim().toLowerCase();
    if (t === "preset") { target = h.closest(".rt-section"); break; }
  }
  if (!target) {
    for (const h of headers) {
      const t = (h.textContent || "").trim().toLowerCase();
      if (t === "sampling") { target = h.closest(".rt-section"); break; }
    }
  }
  if (!target) return null;

  host = document.createElement("div");
  host.className = "presets-bar";
  host.innerHTML = `
    <select class="presets-select" aria-label="Preset"></select>
    <button type="button" class="icon-btn presets-save" title="Save current as preset">+</button>
    <button type="button" class="icon-btn presets-delete" title="Delete preset" disabled>×</button>
  `;
  // Insert directly after the section head and before the params form.
  const head = target.querySelector(".rt-section-head");
  if (head) head.insertAdjacentElement("afterend", host);
  else target.prepend(host);

  select = host.querySelector(".presets-select");
  saveBtn = host.querySelector(".presets-save");
  deleteBtn = host.querySelector(".presets-delete");

  select.addEventListener("change", () => applyPreset(select.value));
  saveBtn.addEventListener("click", () => {
    const name = (window.prompt("Save preset as:") || "").trim();
    if (!name) return;
    if (BUILTINS[name]) {
      alert(`"${name}" is a built-in preset; pick a different name.`);
      return;
    }
    const map = loadUserPresets();
    map[name] = { ...captureCurrent(), builtin: false, savedAt: Date.now() };
    saveUserPresets(map);
    rebuildSelect();
    select.value = name;
    try { localStorage.setItem("presets_v1_active", name); } catch {}
    refreshButtons(name);
  });
  deleteBtn.addEventListener("click", () => {
    const name = select.value;
    if (BUILTINS[name]) return;
    if (!confirm(`Delete preset "${name}"?`)) return;
    const map = loadUserPresets();
    delete map[name];
    saveUserPresets(map);
    rebuildSelect();
    // Selection falls back to whatever rebuildSelect picked.
    applyPreset(select.value);
  });

  return host;
}

export function mount(args) {
  bridge = args;
  if (!build()) return;
  rebuildSelect();
  // On launch, if a non-default preset was last active, leave the loaded
  // params alone -- loadParams already restored them from "params". The
  // dropdown just reflects the last-active label.
}

export { allPresets };
