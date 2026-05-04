// Loopback HTTP client for the Python sidecar.
//
// Resolves the bearer token + port lazily from the preload bridge so feature
// modules can call ``sidecarFetch("/info")`` without each one wiring up the
// IPC handshake. The first call awaits the bridge; subsequent calls reuse
// the cached info.
//
// The renderer-wide `sidecar` global in main.js predates this module. Both
// can coexist for now -- main.js keeps its own copy for the chat hot path,
// and feature modules added in later phases consume this one.

let infoPromise = null;

export function getSidecarInfo() {
  if (!infoPromise) {
    if (!window.cyllama || !window.cyllama.getSidecarInfo) {
      return Promise.reject(new Error("preload bridge missing window.cyllama"));
    }
    infoPromise = window.cyllama.getSidecarInfo();
  }
  return infoPromise;
}

export function resetSidecarInfo() {
  infoPromise = null;
}

export async function sidecarUrl(path) {
  const info = await getSidecarInfo();
  return `http://127.0.0.1:${info.port}${path}`;
}

export async function sidecarFetch(path, options = {}) {
  const info = await getSidecarInfo();
  const headers = {
    ...(options.headers || {}),
    authorization: `Bearer ${info.token}`,
  };
  return fetch(`http://127.0.0.1:${info.port}${path}`, { ...options, headers });
}

export async function sidecarJson(path, body, options = {}) {
  const res = await sidecarFetch(path, {
    method: "POST",
    ...options,
    headers: {
      ...(options.headers || {}),
      "content-type": "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    let detail = "";
    try { detail = await res.text(); } catch {}
    throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
  }
  return res.json();
}

export async function getInfo() {
  const res = await sidecarFetch("/info");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
