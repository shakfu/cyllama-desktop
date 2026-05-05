// Minimal pane router. Each top-level surface (Chat, Documents, ...) lives
// inside one or more elements tagged ``data-pane="<name>"``. The active
// pane is tracked on ``<div id="app" data-pane="<name>">`` and CSS hides
// the inactive ones.
//
// Nav-rail buttons opt in via ``data-pane-target="<name>"``. Console
// remains a separate concern (it's an overlay drawer, not a pane).

const STORAGE_KEY = "active_pane";
const VALID = new Set();
const onShowHandlers = new Map();
let appEl = null;

export function registerPane(name, { onShow } = {}) {
  VALID.add(name);
  if (typeof onShow === "function") onShowHandlers.set(name, onShow);
}

export function setActivePane(name) {
  if (!VALID.has(name)) return;
  if (!appEl) appEl = document.getElementById("app");
  if (!appEl) return;
  appEl.dataset.pane = name;

  // Toggle nav-rail active state for buttons that opt into the router.
  document.querySelectorAll(".nav-btn[data-pane-target]").forEach((b) => {
    b.classList.toggle("active", b.dataset.paneTarget === name);
  });

  try { localStorage.setItem(STORAGE_KEY, name); } catch (_) {}
  const cb = onShowHandlers.get(name);
  if (cb) try { cb(); } catch (e) { console.error(`pane onShow ${name}:`, e); }
}

export function getActivePane() {
  return appEl?.dataset?.pane || "chat";
}

export function init() {
  appEl = document.getElementById("app");
  if (!appEl) return;
  document.querySelectorAll(".nav-btn[data-pane-target]").forEach((btn) => {
    btn.addEventListener("click", () => setActivePane(btn.dataset.paneTarget));
  });
  let initial = "chat";
  try { initial = localStorage.getItem(STORAGE_KEY) || "chat"; } catch (_) {}
  if (!VALID.has(initial)) initial = "chat";
  setActivePane(initial);
}
