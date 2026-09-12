// External chat providers: catalog, model lists, last-used model.
//
// Pure data access plus localStorage, no DOM. The picker consumes this; the
// sidecar holds the keys and makes every outbound call, so nothing here ever
// touches a credential.

import { sidecarFetch, getInfo } from "./sidecar.js";

// The three named kinds. A ``compat`` endpoint is user-defined and comes
// from settings.json instead.
export const NAMED_KINDS = [
  { kind: "openai", label: "OpenAI", account: "openai" },
  { kind: "anthropic", label: "Anthropic", account: "anthropic" },
  { kind: "openrouter", label: "OpenRouter", account: "openrouter" },
];

// Mirrors providers._normalize_account_suffix in the sidecar. A compat
// endpoint's credential slot and model cache are keyed by its name, so the
// two sides must agree on the normalization.
export function normalizeAccountSuffix(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function accountFor(ref) {
  if (!ref) return "";
  if (ref.kind === "compat") return "compat." + normalizeAccountSuffix(ref.name);
  return ref.kind;
}

export function displayName(ref) {
  if (!ref) return "";
  const named = NAMED_KINDS.find((k) => k.kind === ref.kind);
  return named ? named.label : ref.name || ref.kind;
}

/** Provider refs the user can actually reach: a key is configured for them.
 *
 * ``configured`` comes from the main process (which accounts hold a key);
 * ``endpoints`` from settings.json; ``/info.remote.sdks`` says which client
 * library this build can import. A provider missing any of the three is
 * omitted rather than shown disabled -- Preferences is where keys are
 * managed, and a dead row in the picker only invites a click that 401s or
 * 501s.
 */
export async function listAvailable() {
  const out = [];
  let configured = [];
  let endpoints = [];
  // Assume both SDKs present when /info is unreachable: the sidecar is the
  // authority and it will say 501 if it cannot serve the request. Hiding the
  // rows on a failed probe would be the worse error.
  let sdks = { openai: true, anthropic: true };
  try {
    const state = await window.cyllama?.providers?.list();
    configured = (state && state.configured) || [];
  } catch { /* no bridge -> no providers */ }
  try {
    const settings = await window.cyllama?.settings?.get();
    endpoints = (settings && settings.provider_endpoints) || [];
  } catch { /* settings unreadable -> named kinds only */ }
  try {
    const info = await getInfo();
    if (info && info.remote && info.remote.sdks) sdks = info.remote.sdks;
  } catch { /* see the default above */ }

  // Only Anthropic needs its own client; the other three share the OpenAI one.
  const usable = (kind) => (kind === "anthropic" ? sdks.anthropic : sdks.openai);

  for (const k of NAMED_KINDS) {
    if (configured.includes(k.account) && usable(k.kind)) {
      out.push({ kind: k.kind, name: "", base_url: "" });
    }
  }
  if (usable("compat")) {
    for (const e of endpoints) {
      const ref = { kind: "compat", name: e.name, base_url: e.base_url };
      if (configured.includes(accountFor(ref))) out.push(ref);
    }
  }
  return out;
}

/** Chat models this provider offers. Cache-first in the sidecar. */
export async function listModels(ref, { refresh = false } = {}) {
  const q = new URLSearchParams({ kind: ref.kind });
  if (ref.kind === "compat") {
    q.set("name", ref.name || "");
    q.set("base_url", ref.base_url || "");
  }
  if (refresh) q.set("refresh", "true");
  const res = await sidecarFetch(`/providers/models?${q.toString()}`);
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// Last model used per provider, so switching back restores that provider's
// own choice rather than a global one. Same shape as the local path's
// ``last_model_path``.
const LAST_MODEL_PREFIX = "provider.last_model.";

export function lastModel(ref) {
  try {
    return localStorage.getItem(LAST_MODEL_PREFIX + accountFor(ref)) || "";
  } catch {
    return "";
  }
}

export function rememberModel(ref, model) {
  try {
    if (model) localStorage.setItem(LAST_MODEL_PREFIX + accountFor(ref), model);
    else localStorage.removeItem(LAST_MODEL_PREFIX + accountFor(ref));
  } catch { /* private mode / quota -- the picker still works */ }
}

// The active remote backend, so a reload comes back where it left off. Held
// as one JSON blob: the ref plus the chosen model.
const ACTIVE_KEY = "provider.active";

export function loadActive() {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.kind && parsed.model) return parsed;
  } catch { /* malformed -> no remote backend */ }
  return null;
}

export function saveActive(active) {
  try {
    if (active) localStorage.setItem(ACTIVE_KEY, JSON.stringify(active));
    else localStorage.removeItem(ACTIVE_KEY);
  } catch { /* see rememberModel */ }
}
