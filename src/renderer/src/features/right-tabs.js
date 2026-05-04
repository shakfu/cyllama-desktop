// Right-sidebar tab router.
//
// Three tabs: models, agents, general. The tab strip lives in the params-
// panel; each tab body is a sibling section toggled hidden/visible. Active
// tab persisted to localStorage so the user lands on the same tab next
// launch.

const STORAGE_KEY = "right_tab_active";
const VALID = new Set(["models", "agents", "general"]);
let current = "models";
const listeners = new Set();

export function getActive() { return current; }

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setActive(name) {
  if (!VALID.has(name)) return;
  current = name;
  for (const tab of document.querySelectorAll(".rt-tab")) {
    const match = tab.dataset.tab === name;
    tab.classList.toggle("active", match);
    tab.setAttribute("aria-selected", match ? "true" : "false");
  }
  for (const pane of document.querySelectorAll(".rt-pane")) {
    const match = pane.dataset.tab === name;
    pane.classList.toggle("active", match);
    pane.hidden = !match;
  }
  try { localStorage.setItem(STORAGE_KEY, name); } catch {}
  for (const fn of listeners) {
    try { fn(name); } catch { /* listener bug shouldn't break the router */ }
  }
}

export function bind() {
  for (const tab of document.querySelectorAll(".rt-tab")) {
    tab.addEventListener("click", () => setActive(tab.dataset.tab));
  }
  // Nav-rail buttons that jump to a specific tab (e.g. cog -> general).
  for (const btn of document.querySelectorAll("[data-tab-jump]")) {
    btn.addEventListener("click", () => setActive(btn.dataset.tabJump));
  }

  // Restore last tab. Falls through to default 'models' on bad/missing.
  let saved = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch {}
  if (saved && VALID.has(saved)) setActive(saved);
}
