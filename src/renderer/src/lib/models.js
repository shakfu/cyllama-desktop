// Thin wrappers around the sidecar /models/* + /info routes. Pure data
// access; no DOM. Workspaces and the chat ModelPicker consume this.

import { sidecarFetch, sidecarJson, getInfo } from "./sidecar.js";
import { startJob } from "./jobs.js";

// Pickers narrow the catalog by capability. Pass ``kinds: ["chat"]``
// to a chat-side picker, ``kinds: ["mmproj"]`` for the projector, etc.
// ``unknown`` always passes the filter server-side -- a misclassified
// model still shows up so the user isn't locked out by a heuristic
// failure (we keep the "Show all" sentinel as the explicit override).
export async function listModels(opts = {}) {
  let q = "";
  if (Array.isArray(opts.kinds) && opts.kinds.length) {
    q = `?kinds=${encodeURIComponent(opts.kinds.join(","))}`;
  } else if (opts.kinds === "all") {
    q = "?kinds=all";
  }
  const res = await sidecarFetch(`/models/cached${q}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function inspectModel(path) {
  return sidecarJson("/models/inspect", { path });
}

export async function importModel(path) {
  return sidecarJson("/models/import", { path });
}

export async function hfPeek({ url, repo, file, target, revision }) {
  return sidecarJson("/models/hf/peek", { url, repo, file, target, revision });
}

export async function hfDownload({ url, repo, file, target, revision }) {
  // Returns a JobHandle. Caller subscribes to events for progress.
  return startJob("models.hf-download", { url, repo, file, target, revision });
}

export { getInfo };
