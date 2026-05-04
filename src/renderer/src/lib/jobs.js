// Client for the sidecar /jobs/* family.
//
// Long-running operations (HF download, RAG ingest, image generation, batch
// jobs, model quantize) all go through this single primitive so the
// renderer has one progress / cancel / result contract. Per PLAN.md
// Section 7, every long-job endpoint POSTs to /jobs/<kind> and returns a
// {job_id}; events flow back over /jobs/<id>/events as SSE.
//
// Usage:
//   const job = await startJob("demo", { steps: 5 });
//   const off = job.onEvent((ev) => { ... });
//   await job.done;            // resolves with the final 'result' event payload
//   off();
//
// Cancellation:
//   await job.cancel();        // POST /jobs/<id>/cancel
//   The event stream still ends with a 'done' event after cancellation.

import { sidecarFetch, sidecarJson } from "./sidecar.js";

export class JobHandle {
  constructor(id, kind) {
    this.id = id;
    this.kind = kind;
    this._listeners = new Set();
    this._abort = null;
    this._done = false;
    this._result = null;
    this._error = null;
    this.done = new Promise((resolve, reject) => {
      this._resolve = resolve;
      this._reject = reject;
    });
    // Suppress UnhandledPromiseRejection if the caller never awaits .done.
    this.done.catch(() => {});
  }

  onEvent(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  async cancel() {
    if (this._done) return;
    if (this._abort) {
      // Closing the SSE on the client doesn't cancel the job server-side;
      // we still POST /cancel for that. The abort is just to free the fetch.
    }
    try {
      await sidecarFetch(`/jobs/${this.id}/cancel`, { method: "POST" });
    } catch (e) {
      // Cancel is best-effort; surface to listeners if they care.
      this._emit({ type: "cancel-error", message: e.message });
    }
  }

  _emit(ev) {
    if (ev && ev.type === "result") this._result = ev.result;
    if (ev && ev.type === "error") this._error = ev.message || "job failed";
    if (ev && ev.type === "done") {
      this._done = true;
      if (this._error) this._reject(new Error(this._error));
      else this._resolve(this._result);
    }
    for (const fn of this._listeners) {
      try { fn(ev); } catch { /* listener bug shouldn't kill the stream */ }
    }
  }

  async _consume() {
    this._abort = new AbortController();
    let res;
    try {
      res = await sidecarFetch(`/jobs/${this.id}/events`, {
        signal: this._abort.signal,
      });
    } catch (e) {
      this._emit({ type: "error", message: e.message });
      this._emit({ type: "done" });
      return;
    }
    if (!res.ok || !res.body) {
      this._emit({ type: "error", message: `HTTP ${res.status}` });
      this._emit({ type: "done" });
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            let ev;
            try { ev = JSON.parse(line.slice(6)); } catch { continue; }
            this._emit(ev);
            if (ev.type === "done") return;
          }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        this._emit({ type: "error", message: e.message });
      }
    } finally {
      if (!this._done) this._emit({ type: "done" });
    }
  }
}

export async function startJob(kind, body = {}) {
  const r = await sidecarJson(`/jobs/${kind}`, body);
  if (!r || typeof r.job_id !== "string") {
    throw new Error("malformed job response: missing job_id");
  }
  const handle = new JobHandle(r.job_id, kind);
  // Fire-and-forget: events flow to listeners as they arrive.
  handle._consume();
  return handle;
}

export async function listJobs() {
  const res = await sidecarFetch("/jobs");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
