// External chat providers: catalog, model lists, last-used model.
//
// Pure data access plus localStorage, no DOM. The picker consumes this; the
// sidecar holds the keys and makes every outbound call, so nothing here ever
// touches a credential.

import { sidecarFetch, getInfo } from "./sidecar.js";
import { NAMED_PROVIDERS } from "../../../shared/provider-identity.js";

// A compat ref's ``account`` comes from the main process (providers:list),
// which owns the normalization rule.
export function accountFor(ref) {
  if (!ref) return "";
  return ref.kind === "compat" ? ref.account || "" : ref.kind;
}

export function displayName(ref) {
  if (!ref) return "";
  const named = NAMED_PROVIDERS.find((k) => k.kind === ref.kind);
  return named ? named.label : ref.name || ref.kind;
}

/** Provider refs the user can actually reach: a key is configured for them.
 *
 * ``configured`` and ``endpoints`` come from the main process (which accounts
 * hold a key, and each endpoint's account and ``needs_key``);
 * ``/info.remote.sdks`` says which client
 * library this build can import. A provider missing any of the three is
 * omitted rather than shown disabled -- a dead row only invites a click
 * that 401s or 501s. The picker carries a permanent row into Preferences
 * instead, so the feature stays discoverable without per-provider clutter.
 *
 * A loopback compat endpoint needs no key and appears as soon as it is
 * added.
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
    endpoints = (state && state.endpoints) || [];
  } catch { /* no bridge -> no providers */ }
  try {
    const info = await getInfo();
    if (info && info.remote && info.remote.sdks) sdks = info.remote.sdks;
  } catch { /* see the default above */ }

  // Only Anthropic needs its own client; the other three share the OpenAI one.
  const usable = (kind) => (kind === "anthropic" ? sdks.anthropic : sdks.openai);

  for (const k of NAMED_PROVIDERS) {
    if (configured.includes(k.account) && usable(k.kind)) {
      out.push({ kind: k.kind, name: "", base_url: "", account: k.account });
    }
  }
  if (usable("compat")) {
    for (const e of endpoints) {
      if (configured.includes(e.account) || !e.needs_key) {
        out.push({ kind: "compat", name: e.name, base_url: e.base_url, account: e.account });
      }
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
