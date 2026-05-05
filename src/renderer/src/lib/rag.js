// Thin wrappers around the sidecar /rag/* routes. Pure data access; no DOM.

import { sidecarFetch, sidecarJson } from "./sidecar.js";
import { startJob } from "./jobs.js";

export async function listCollections() {
  const r = await sidecarFetch("/rag/collections");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function createCollection({ name, embedding_model_path }) {
  return sidecarJson("/rag/collections", { name, embedding_model_path });
}

export async function deleteCollection(id) {
  const r = await sidecarFetch(`/rag/collections/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function ingest({ collection_id, paths, glob, chunk_size, chunk_overlap }) {
  return startJob("rag.ingest", { collection_id, paths, glob, chunk_size, chunk_overlap });
}

/**
 * Stream a RAG query as SSE. Returns an async iterator wrapper that calls
 * onSources({sources}) once and onToken(text) per chunk; resolves on
 * [DONE] or rejects on error.
 *
 *   const ctrl = new AbortController();
 *   await query({collection_id, generation_model_path, question, ...opts},
 *               { signal: ctrl.signal,
 *                 onSources: ({sources}) => ...,
 *                 onToken: (t) => ... });
 */
export async function query(body, { signal, onSources, onToken } = {}) {
  const res = await sidecarFetch("/rag/query", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ""}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, nl);
      buf = buf.slice(nl + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);
        if (payload === "[DONE]") return;
        let ev;
        try { ev = JSON.parse(payload); } catch { continue; }
        if (ev.error) throw new Error(ev.error);
        if (ev.sources && onSources) onSources(ev);
        else if (ev.text != null && onToken) onToken(ev.text);
      }
    }
  }
}
