// ModelPicker: dropdown attached to the chat top-bar pill. Replaces the
// file-dialog-only flow. Lists cached models grouped by source, with a
// "Browse..." item at the bottom that falls back to the OS file picker.

import { listModels } from "../lib/models.js";
import * as providers from "../lib/providers.js";

let onPick = (path) => {};
let onPickRemote = (ref, model) => {};
let onManageProviders = () => {};
let onBrowse = async () => null;
let pillEl = null;
let menuEl = null;
let openState = false;

function fmtBytes(n) {
  if (!Number.isFinite(n)) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${u[i]}`;
}

function ensureMenu() {
  if (menuEl) return menuEl;
  menuEl = document.createElement("div");
  menuEl.className = "mp-menu";
  menuEl.hidden = true;
  document.body.appendChild(menuEl);
  return menuEl;
}

function position() {
  if (!pillEl || !menuEl) return;
  const rect = pillEl.getBoundingClientRect();
  menuEl.style.top = `${rect.bottom + 6}px`;
  menuEl.style.left = `${rect.left}px`;
  menuEl.style.minWidth = `${Math.max(280, rect.width)}px`;
}

// Second view: the chosen provider's models. Cache-first, so this is
// usually instant; a cold cache pays one fetch. Free-text entry stays
// available because a provider's list can lag a new model id.
async function renderProviderModels(ref) {
  const menu = ensureMenu();
  menu.innerHTML = "";

  const back = document.createElement("button");
  back.type = "button";
  back.className = "mp-item mp-item-back";
  back.textContent = "< All models";
  back.addEventListener("click", () => { render().then(position); });
  menu.appendChild(back);

  const head = document.createElement("div");
  head.className = "mp-group";
  head.textContent = providers.displayName(ref);
  menu.appendChild(head);

  const listHost = document.createElement("div");
  menu.appendChild(listHost);

  function activate(model) {
    const id = String(model || "").trim();
    if (!id) return;
    close();
    onPickRemote(ref, id);
  }

  // Free text first: it always works, including for a model the list
  // does not carry yet.
  const form = document.createElement("form");
  form.className = "mp-freetext";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "model id";
  input.value = providers.lastModel(ref);
  const go = document.createElement("button");
  go.type = "submit";
  go.className = "btn-mini";
  go.textContent = "Use";
  form.append(input, go);
  form.addEventListener("submit", (e) => { e.preventDefault(); activate(input.value); });
  menu.appendChild(form);

  async function fill({ refresh = false } = {}) {
    listHost.innerHTML = `<div class="mp-loading">Loading...</div>`;
    let payload;
    try {
      payload = await providers.listModels(ref, { refresh });
    } catch (e) {
      listHost.innerHTML = "";
      const err = document.createElement("div");
      err.className = "mp-error";
      err.textContent = `Error: ${e.message}`;
      listHost.appendChild(err);
      return;
    }
    listHost.innerHTML = "";
    const items = payload.models || [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "mp-empty";
      empty.textContent = "No models listed. Type an id above.";
      listHost.appendChild(empty);
    }
    for (const m of items) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "mp-item";
      row.innerHTML = `<span class="mp-item-name"></span><span class="mp-item-meta"></span>`;
      row.querySelector(".mp-item-name").textContent = m.display_name || m.id;
      row.querySelector(".mp-item-meta").textContent =
        Number.isFinite(m.context_length) ? `${Math.round(m.context_length / 1024)}k ctx` : "";
      row.title = m.id;
      row.addEventListener("click", () => activate(m.id));
      listHost.appendChild(row);
    }
    const foot = document.createElement("div");
    foot.className = "mp-foot";
    // A stale list is served on purpose when a refresh fails, so say which
    // one is on screen rather than leaving the picker silently out of date.
    foot.textContent = payload.stale ? "list may be out of date" : "";
    const refreshBtn = document.createElement("button");
    refreshBtn.type = "button";
    refreshBtn.className = "btn-mini";
    refreshBtn.textContent = "Refresh";
    refreshBtn.addEventListener("click", () => fill({ refresh: true }));
    foot.appendChild(refreshBtn);
    listHost.appendChild(foot);
  }

  await fill();
  position();
}

async function render() {
  const menu = ensureMenu();
  menu.innerHTML = `<div class="mp-loading">Loading...</div>`;
  let remote = [];
  try { remote = await providers.listAvailable(); } catch { remote = []; }
  let models = [];
  let localError = "";
  try {
    // Chat-side picker: only surface text-generation models. Whisper
    // / SD / mmproj projectors aren't loadable as a chat LLM and used
    // to clutter the dropdown.
    const r = await listModels({ kinds: ["chat"] });
    models = r.models || [];
  } catch (e) {
    // Report it inline rather than replacing the whole menu: a failed
    // local scan must not hide the providers, which are still reachable.
    localError = e.message;
  }

  menu.innerHTML = "";
  const groupBy = { local: [], hf: [] };
  for (const m of models) (groupBy[m.source] || groupBy.local).push(m);

  function rowFor(m) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "mp-item";
    row.innerHTML = `
      <span class="mp-item-name"></span>
      <span class="mp-item-meta"></span>
    `;
    row.querySelector(".mp-item-name").textContent = m.name;
    row.querySelector(".mp-item-meta").textContent = fmtBytes(m.size);
    row.title = m.path;
    row.addEventListener("click", () => { close(); onPick(m.path); });
    return row;
  }

  if (remote.length) {
    const h = document.createElement("div");
    h.className = "mp-group";
    h.textContent = "Providers";
    menu.appendChild(h);
    for (const ref of remote) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "mp-item";
      row.innerHTML = `<span class="mp-item-name"></span><span class="mp-item-meta"></span>`;
      row.querySelector(".mp-item-name").textContent = providers.displayName(ref);
      // The last model used with this provider, so the common case is one
      // click. Empty until the user has picked one.
      row.querySelector(".mp-item-meta").textContent = providers.lastModel(ref) || "choose model";
      row.title = ref.kind === "compat" ? ref.base_url : "";
      row.addEventListener("click", () => { renderProviderModels(ref); });
      menu.appendChild(row);
    }
  }

  if (localError) {
    const err = document.createElement("div");
    err.className = "mp-error";
    err.textContent = `Local models unavailable: ${localError}`;
    menu.appendChild(err);
  }

  if (groupBy.local.length) {
    const h = document.createElement("div");
    h.className = "mp-group";
    h.textContent = "Local";
    menu.appendChild(h);
    for (const m of groupBy.local) menu.appendChild(rowFor(m));
  }
  if (groupBy.hf.length) {
    const h = document.createElement("div");
    h.className = "mp-group";
    h.textContent = "HuggingFace cache";
    menu.appendChild(h);
    for (const m of groupBy.hf) menu.appendChild(rowFor(m));
  }
  if (!groupBy.local.length && !groupBy.hf.length && !remote.length) {
    const empty = document.createElement("div");
    empty.className = "mp-empty";
    empty.textContent = "No models found. Use Browse... or open the Models tab.";
    menu.appendChild(empty);
  }

  // Action rows. These are the only mention of providers a user with local
  // models would ever see -- without this row the feature is invisible
  // unless you already know to look in Preferences.
  const sep = document.createElement("div");
  sep.className = "mp-sep";
  menu.appendChild(sep);
  const manage = document.createElement("button");
  manage.type = "button";
  manage.className = "mp-item mp-item-browse";
  manage.textContent = remote.length ? "Manage providers..." : "Add a provider...";
  manage.addEventListener("click", () => { close(); onManageProviders(); });
  menu.appendChild(manage);
  const browse = document.createElement("button");
  browse.type = "button";
  browse.className = "mp-item mp-item-browse";
  browse.textContent = "Browse...";
  browse.addEventListener("click", async () => {
    close();
    try {
      const path = await onBrowse();
      if (path) onPick(path);
    } catch { /* user cancelled */ }
  });
  menu.appendChild(browse);
}

export async function open() {
  ensureMenu();
  await render();
  position();
  menuEl.hidden = false;
  openState = true;
  setTimeout(() => {
    document.addEventListener("mousedown", outsideHandler, { capture: true });
    window.addEventListener("resize", position);
  }, 0);
}

export function close() {
  if (!openState) return;
  openState = false;
  if (menuEl) menuEl.hidden = true;
  document.removeEventListener("mousedown", outsideHandler, { capture: true });
  window.removeEventListener("resize", position);
}

function outsideHandler(e) {
  if (!menuEl) return;
  if (menuEl.contains(e.target)) return;
  if (pillEl && pillEl.contains(e.target)) return;
  close();
}

export function bind({
  pillSelector, onPickPath, onPickProvider, onManageProviders: onManage, onBrowsePath,
} = {}) {
  pillEl = document.querySelector(pillSelector || "#pick");
  if (!pillEl) return;
  if (typeof onPickPath === "function") onPick = onPickPath;
  if (typeof onPickProvider === "function") onPickRemote = onPickProvider;
  if (typeof onManage === "function") onManageProviders = onManage;
  if (typeof onBrowsePath === "function") onBrowse = onBrowsePath;
  pillEl.addEventListener("click", (e) => {
    e.preventDefault();
    if (openState) close();
    else open();
  });
}
