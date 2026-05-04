// Thin wrappers around the sidecar /models/* + /info routes. Pure data
// access; no DOM. Workspaces and the chat ModelPicker consume this.

import { sidecarFetch, sidecarJson, getInfo } from "./sidecar.js";
import { startJob } from "./jobs.js";

export async function listModels() {
  const res = await sidecarFetch("/models/cached");
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
