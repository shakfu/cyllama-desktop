// Right-sidebar tab router.
//
// Phase F.2: the Agents tab moved to a full-area pane (nav-rail icon).
// Parameters ("models") is now the only right-sidebar surface; the
// router stays in place so the existing setActive/getActive API
// continues to compose with the Preferences window and other consumers.
//
// "general" is also retired (the General tab moved to the Preferences
// window earlier). "agents" is retained as a recognised name so any
// stale localStorage value from a prior install round-trips cleanly
// to "models".

const STORAGE_KEY = "right_tab_active";
const VALID = new Set(["models"]);
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
