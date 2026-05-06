// ModelPicker: dropdown attached to the chat top-bar pill. Replaces the
// file-dialog-only flow. Lists cached models grouped by source, with a
// "Browse..." item at the bottom that falls back to the OS file picker.

import { listModels } from "../lib/models.js";

let onPick = (path) => {};
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

async function render() {
  const menu = ensureMenu();
  menu.innerHTML = `<div class="mp-loading">Loading...</div>`;
  let models = [];
  try {
    // Chat-side picker: only surface text-generation models. Whisper
    // / SD / mmproj projectors aren't loadable as a chat LLM and used
    // to clutter the dropdown.
    const r = await listModels({ kinds: ["chat"] });
    models = r.models || [];
  } catch (e) {
    menu.innerHTML = `<div class="mp-error">Error: ${e.message}</div>`;
    return;
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
  if (!groupBy.local.length && !groupBy.hf.length) {
    const empty = document.createElement("div");
    empty.className = "mp-empty";
    empty.textContent = "No models found. Use Browse... or open the Models tab.";
    menu.appendChild(empty);
  }

  // Browse... fallback to OS file dialog.
  const sep = document.createElement("div");
  sep.className = "mp-sep";
  menu.appendChild(sep);
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

export function bind({ pillSelector, onPickPath, onBrowsePath } = {}) {
  pillEl = document.querySelector(pillSelector || "#pick");
  if (!pillEl) return;
  if (typeof onPickPath === "function") onPick = onPickPath;
  if (typeof onBrowsePath === "function") onBrowse = onBrowsePath;
  pillEl.addEventListener("click", (e) => {
    e.preventDefault();
    if (openState) close();
    else open();
  });
}
